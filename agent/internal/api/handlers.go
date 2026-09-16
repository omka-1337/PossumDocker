package api

import (
	"encoding/json"
	"io"
	"net/http"

	"github.com/omka-1337/PossumDocker/agent/internal/runtime"
)

// --- runtime --------------------------------------------------------------------

func (s *Server) states(w http.ResponseWriter, r *http.Request) {
	states, err := s.runtime.States(r.Context())
	if err != nil {
		s.fail(w, r, err)
		return
	}
	writeJSON(w, states)
}

func (s *Server) publishedPorts(w http.ResponseWriter, r *http.Request) {
	ports, err := s.runtime.PublishedPorts(r.Context())
	if err != nil {
		s.fail(w, r, err)
		return
	}
	writeJSON(w, ports)
}

func (s *Server) state(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	state, err := s.runtime.State(r.Context(), id)
	if err != nil {
		s.fail(w, r, err)
		return
	}
	if state == nil {
		writeError(w, http.StatusNotFound, "no container for this server")
		return
	}
	writeJSON(w, state)
}

func (s *Server) pull(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Image string `json:"image"`
	}
	if !decode(w, r, &body) {
		return
	}
	out := newNDJSON(w)
	out.finish(s.runtime.Pull(r.Context(), body.Image, func(line string) { out.send(map[string]string{"log": line}) }))
}

func (s *Server) ensureVolume(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	if err := s.runtime.EnsureVolume(r.Context(), id); err != nil {
		s.fail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) install(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	var spec runtime.ContainerSpec
	if !decode(w, r, &spec) || !validSpec(w, spec) {
		return
	}
	out := newNDJSON(w)
	out.finish(s.runtime.RunInstall(r.Context(), id, spec, func(line string) { out.send(map[string]string{"log": line}) }))
}

func (s *Server) create(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	var spec runtime.ContainerSpec
	if !decode(w, r, &spec) || !validSpec(w, spec) {
		return
	}
	if err := s.runtime.Create(r.Context(), id, spec); err != nil {
		s.fail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// validSpec answers 400 for a spec that isn't well formed. The panel only sends specs rendered from
// templates; this is for the day it's compromised: no mount outside the server's own volume.
func validSpec(w http.ResponseWriter, spec runtime.ContainerSpec) bool {
	if err := spec.Validate(); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return false
	}
	return true
}

func (s *Server) start(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	if err := s.runtime.Start(r.Context(), id); err != nil {
		s.fail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) stop(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	var body struct {
		Command string `json:"command"`
		Timeout int    `json:"timeout"`
	}
	if !decode(w, r, &body) {
		return
	}
	if err := s.runtime.Stop(r.Context(), id, body.Command, body.Timeout); err != nil {
		s.fail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) command(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	var body struct {
		Line string `json:"line"`
	}
	if !decode(w, r, &body) {
		return
	}
	if err := s.runtime.SendCommand(r.Context(), id, body.Line); err != nil {
		s.fail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// logs streams console lines as text until the container stops (or the panel disconnects).
func (s *Server) logs(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	out := flushingWriter{w, http.NewResponseController(w)}
	tail := cleanTail(r.URL.Query().Get("tail"))
	since := atoiDefault(r.URL.Query().Get("since"), 0)
	err := s.runtime.Logs(r.Context(), id, tail, since, func(line string) { io.WriteString(out, line+"\n") }) //nolint:errcheck
	if err != nil && r.Context().Err() == nil {
		s.log.Warn("log stream ended", "server", id, "err", err)
		abort()
	}
}

func (s *Server) readContainerFile(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	data, err := s.runtime.ReadFile(r.Context(), id, r.URL.Query().Get("path"))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	if data == nil {
		writeError(w, http.StatusNotFound, "no such file")
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Write(data) //nolint:errcheck
}

func (s *Server) writeContainerFile(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	data, err := io.ReadAll(io.LimitReader(r.Body, 16<<20))
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if err := s.runtime.WriteFile(r.Context(), id, r.URL.Query().Get("path"), data); err != nil {
		s.fail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) remove(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	if err := s.runtime.Remove(r.Context(), id, s.files.Close); err != nil {
		s.fail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// --- files --------------------------------------------------------------------------

// fileCall handles the JSON-in, JSON-out file operations.
func (s *Server) fileCall(w http.ResponseWriter, r *http.Request, body any, do func(id string) (any, error)) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	if body != nil && !decode(w, r, body) {
		return
	}
	result, err := do(id)
	if err != nil {
		s.fail(w, r, err)
		return
	}
	if result == nil {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	writeJSON(w, result)
}

type pathBody struct {
	Path string `json:"path"`
}

type transferBody struct {
	Sources     []string `json:"sources"`
	Destination string   `json:"destination"`
}

func (s *Server) list(w http.ResponseWriter, r *http.Request) {
	s.fileCall(w, r, nil, func(id string) (any, error) { return s.files.List(r.Context(), id, r.URL.Query().Get("path")) })
}

func (s *Server) stat(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	entry, err := s.files.Stat(r.Context(), id, r.URL.Query().Get("path"))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	if entry == nil {
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	writeJSON(w, entry)
}

func (s *Server) mkdir(w http.ResponseWriter, r *http.Request) {
	var body pathBody
	s.fileCall(w, r, &body, func(id string) (any, error) { return nil, s.files.Mkdir(r.Context(), id, body.Path) })
}

func (s *Server) ensureDirs(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Paths []string `json:"paths"`
	}
	s.fileCall(w, r, &body, func(id string) (any, error) { return nil, s.files.EnsureDirs(r.Context(), id, body.Paths) })
}

func (s *Server) rename(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Path string `json:"path"`
		Name string `json:"name"`
	}
	s.fileCall(w, r, &body, func(id string) (any, error) { return nil, s.files.Rename(r.Context(), id, body.Path, body.Name) })
}

func (s *Server) move(w http.ResponseWriter, r *http.Request) {
	var body transferBody
	s.fileCall(w, r, &body, func(id string) (any, error) {
		return nil, s.files.Move(r.Context(), id, body.Sources, body.Destination)
	})
}

func (s *Server) copy(w http.ResponseWriter, r *http.Request) {
	var body transferBody
	s.fileCall(w, r, &body, func(id string) (any, error) {
		return nil, s.files.Copy(r.Context(), id, body.Sources, body.Destination)
	})
}

func (s *Server) delete(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Paths []string `json:"paths"`
	}
	s.fileCall(w, r, &body, func(id string) (any, error) { return nil, s.files.Delete(r.Context(), id, body.Paths) })
}

func (s *Server) extract(w http.ResponseWriter, r *http.Request) {
	var body pathBody
	s.fileCall(w, r, &body, func(id string) (any, error) {
		target, err := s.files.Extract(r.Context(), id, body.Path)
		if err != nil {
			return nil, err
		}
		return pathBody{Path: target}, nil
	})
}

func (s *Server) readText(w http.ResponseWriter, r *http.Request) {
	s.fileCall(w, r, nil, func(id string) (any, error) {
		content, err := s.files.ReadText(r.Context(), id, r.URL.Query().Get("path"))
		if err != nil {
			return nil, err
		}
		return map[string]string{"content": content}, nil
	})
}

func (s *Server) writeText(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Path    string `json:"path"`
		Content string `json:"content"`
	}
	s.fileCall(w, r, &body, func(id string) (any, error) {
		return nil, s.files.WriteText(r.Context(), id, body.Path, body.Content)
	})
}

// upload takes a tar body (the panel builds it from the uploaded files) and unpacks it.
func (s *Server) upload(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	if err := s.files.Upload(r.Context(), id, r.URL.Query().Get("directory"), r.Body); err != nil {
		s.fail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) download(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	s.streamBody(w, r, func(out io.Writer) error { return s.files.Download(r.Context(), id, r.URL.Query().Get("path"), out) })
}

func (s *Server) downloadArchive(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	w.Header().Set("Content-Type", "application/gzip")
	names := queryList(r, "name")
	s.streamBody(w, r, func(out io.Writer) error {
		return s.files.DownloadArchive(r.Context(), id, r.URL.Query().Get("path"), names, out)
	})
}

// backup streams a .tar.gz. The paths it holds go in the X-Backup-Paths header ("null": everything).
func (s *Server) backup(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	var body struct {
		Paths   []string `json:"paths"` // null: the whole volume
		Exclude []string `json:"exclude"`
	}
	if !decode(w, r, &body) {
		return
	}
	present, err := s.files.BackupPaths(r.Context(), id, body.Paths)
	if err != nil {
		s.fail(w, r, err)
		return
	}
	header, _ := json.Marshal(present)
	w.Header().Set("X-Backup-Paths", string(header))
	w.Header().Set("Content-Type", "application/gzip")
	s.streamBody(w, r, func(out io.Writer) error { return s.files.Backup(r.Context(), id, present, body.Exclude, out) })
}

func (s *Server) restore(w http.ResponseWriter, r *http.Request) {
	id, ok := serverID(w, r)
	if !ok {
		return
	}
	paths, ok1 := jsonList(r, "paths")
	exclude, ok2 := jsonList(r, "exclude")
	if !ok1 || !ok2 {
		writeError(w, http.StatusBadRequest, "paths and exclude must be JSON lists")
		return
	}
	if err := s.files.Restore(r.Context(), id, paths, exclude, r.Body); err != nil {
		s.fail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) closeHelper(w http.ResponseWriter, r *http.Request) {
	s.fileCall(w, r, nil, func(id string) (any, error) { return nil, s.files.Close(r.Context(), id) })
}

// streamBody sends 200 and streams; a failure after that point drops the connection, so the panel
// sees a broken download instead of a quietly truncated file.
func (s *Server) streamBody(w http.ResponseWriter, r *http.Request, write func(io.Writer) error) {
	w.WriteHeader(http.StatusOK)
	if err := write(flushingWriter{w, http.NewResponseController(w)}); err != nil {
		if r.Context().Err() == nil {
			s.log.Error("stream failed", "path", r.URL.Path, "err", err)
		}
		abort()
	}
}
