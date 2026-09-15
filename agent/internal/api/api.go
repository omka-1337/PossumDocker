// Package api is the agent's HTTP API. Only the panel calls it, with a shared token.
//
// JSON in and out. Long operations stream NDJSON lines ({"log": "..."} and finally {"error": ...}
// or {"done": true}); console logs stream plain text lines; file downloads and backups stream bytes.
package api

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/omka-1337/DockerGameServer/agent/internal/engine"
	"github.com/omka-1337/DockerGameServer/agent/internal/files"
	"github.com/omka-1337/DockerGameServer/agent/internal/runtime"
)

type Server struct {
	token   string
	runtime *runtime.Runtime
	files   *files.Files
	log     *slog.Logger
}

func New(token string, rt *runtime.Runtime, fs *files.Files, log *slog.Logger) *Server {
	return &Server{token: token, runtime: rt, files: fs, log: log}
}

// Handler returns all routes behind token authentication (except /health).
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, map[string]string{"status": "ok"}) })

	api := http.NewServeMux()
	// Runtime: containers and their lifecycle.
	api.HandleFunc("GET /v1/servers", s.states)
	api.HandleFunc("GET /v1/ports", s.publishedPorts)
	api.HandleFunc("POST /v1/images/pull", s.pull)
	api.HandleFunc("GET /v1/servers/{id}", s.state)
	api.HandleFunc("DELETE /v1/servers/{id}", s.remove)
	api.HandleFunc("POST /v1/servers/{id}/volume", s.ensureVolume)
	api.HandleFunc("POST /v1/servers/{id}/install", s.install)
	api.HandleFunc("PUT /v1/servers/{id}/container", s.create)
	api.HandleFunc("POST /v1/servers/{id}/start", s.start)
	api.HandleFunc("POST /v1/servers/{id}/stop", s.stop)
	api.HandleFunc("POST /v1/servers/{id}/command", s.command)
	api.HandleFunc("GET /v1/servers/{id}/logs", s.logs)
	api.HandleFunc("GET /v1/servers/{id}/container-file", s.readContainerFile)
	api.HandleFunc("PUT /v1/servers/{id}/container-file", s.writeContainerFile)
	// Files: the data volume, through the helper container.
	api.HandleFunc("GET /v1/servers/{id}/files", s.list)
	api.HandleFunc("GET /v1/servers/{id}/files/stat", s.stat)
	api.HandleFunc("POST /v1/servers/{id}/files/mkdir", s.mkdir)
	api.HandleFunc("POST /v1/servers/{id}/files/ensure-dirs", s.ensureDirs)
	api.HandleFunc("POST /v1/servers/{id}/files/rename", s.rename)
	api.HandleFunc("POST /v1/servers/{id}/files/move", s.move)
	api.HandleFunc("POST /v1/servers/{id}/files/copy", s.copy)
	api.HandleFunc("POST /v1/servers/{id}/files/delete", s.delete)
	api.HandleFunc("POST /v1/servers/{id}/files/extract", s.extract)
	api.HandleFunc("GET /v1/servers/{id}/files/text", s.readText)
	api.HandleFunc("PUT /v1/servers/{id}/files/text", s.writeText)
	api.HandleFunc("POST /v1/servers/{id}/files/upload", s.upload)
	api.HandleFunc("GET /v1/servers/{id}/files/download", s.download)
	api.HandleFunc("GET /v1/servers/{id}/files/archive", s.downloadArchive)
	api.HandleFunc("POST /v1/servers/{id}/files/backup", s.backup)
	api.HandleFunc("PUT /v1/servers/{id}/files/restore", s.restore)
	api.HandleFunc("DELETE /v1/servers/{id}/files/helper", s.closeHelper)

	mux.Handle("/", s.authenticate(api))
	return s.logRequests(mux)
}

// authenticate lets through requests with the right bearer token and a valid {id}.
func (s *Server) authenticate(next http.Handler) http.Handler {
	expected := []byte("Bearer " + s.token)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Constant time: the comparison must not reveal how much of a guess was right.
		if subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), expected) != 1 {
			writeError(w, http.StatusUnauthorized, "wrong or missing token")
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Server) logRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		recorder := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(recorder, r)
		if r.URL.Path != "/health" {
			s.log.Debug("request", "method", r.Method, "path", r.URL.Path, "status", recorder.status, "took", time.Since(start))
		}
	})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(status int) {
	r.status = status
	r.ResponseWriter.WriteHeader(status)
}

// Unwrap lets http.ResponseController reach Flush on the real writer.
func (r *statusRecorder) Unwrap() http.ResponseWriter { return r.ResponseWriter }

// --- helpers ------------------------------------------------------------------

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v) //nolint:errcheck
}

func writeError(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]string{"error": message}) //nolint:errcheck
}

// fail answers with the status that fits the error.
func (s *Server) fail(w http.ResponseWriter, r *http.Request, err error) {
	var fe *files.Error
	var de *engine.Error
	switch {
	case errors.As(err, &fe):
		writeError(w, fe.Status, fe.Message)
	case errors.As(err, &de) && de.Status == http.StatusNotFound:
		writeError(w, http.StatusNotFound, de.Message)
	case errors.As(err, &de) && de.Status == http.StatusConflict:
		writeError(w, http.StatusConflict, de.Message)
	case errors.Is(err, context.Canceled):
		// The panel went away; nobody reads the answer.
	default:
		s.log.Error("request failed", "method", r.Method, "path", r.URL.Path, "err", err)
		writeError(w, http.StatusBadGateway, err.Error())
	}
}

// serverID reads {id} from the path; false (and an answer sent) when it's not a valid id.
func serverID(w http.ResponseWriter, r *http.Request) (string, bool) {
	id := r.PathValue("id")
	if !runtime.ValidID(id) {
		writeError(w, http.StatusBadRequest, "invalid server id")
		return "", false
	}
	return id, true
}

// decode reads a JSON body into v.
func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	if err := json.NewDecoder(io.LimitReader(r.Body, 8<<20)).Decode(v); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body: "+err.Error())
		return false
	}
	return true
}

// ndjson streams one JSON object per line and flushes each, so the panel sees progress live.
type ndjson struct {
	w  http.ResponseWriter
	rc *http.ResponseController
}

func newNDJSON(w http.ResponseWriter) *ndjson {
	w.Header().Set("Content-Type", "application/x-ndjson")
	w.WriteHeader(http.StatusOK)
	return &ndjson{w: w, rc: http.NewResponseController(w)}
}

func (n *ndjson) send(v any) {
	json.NewEncoder(n.w).Encode(v) //nolint:errcheck
	n.rc.Flush()                   //nolint:errcheck
}

func (n *ndjson) finish(err error) {
	if err != nil {
		n.send(map[string]string{"error": err.Error()})
	} else {
		n.send(map[string]bool{"done": true})
	}
}

// flushingWriter flushes after every write: logs and downloads must not sit in a buffer.
type flushingWriter struct {
	w  io.Writer
	rc *http.ResponseController
}

func (f flushingWriter) Write(p []byte) (int, error) {
	n, err := f.w.Write(p)
	f.rc.Flush() //nolint:errcheck
	return n, err
}

// abort drops the connection mid-stream: the only way to tell the panel a streamed body is broken
// once the 200 status has been sent.
func abort() { panic(http.ErrAbortHandler) }

func queryList(r *http.Request, key string) []string {
	values := r.URL.Query()[key]
	if values == nil {
		return nil
	}
	return values
}

// jsonList parses a JSON list query parameter; "null" or absent means nil.
func jsonList(r *http.Request, key string) ([]string, bool) {
	raw := r.URL.Query().Get(key)
	if raw == "" || raw == "null" {
		return nil, true
	}
	var out []string
	return out, json.Unmarshal([]byte(raw), &out) == nil
}

func atoiDefault(s string, fallback int64) int64 {
	if v, err := strconv.ParseInt(s, 10, 64); err == nil {
		return v
	}
	return fallback
}

func cleanTail(tail string) string {
	if tail == "" || tail == "all" {
		return "all"
	}
	if _, err := strconv.Atoi(strings.TrimSpace(tail)); err != nil {
		return "200"
	}
	return tail
}
