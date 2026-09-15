// Package engine is a small client for the Docker Engine API: only the calls the agent needs.
//
// The official Go SDK is still moving (github.com/moby/moby/client v0.x), and the agent uses about
// fifteen endpoints of a stable, documented HTTP API, so it talks HTTP to the socket directly.
// API reference: https://docs.docker.com/reference/api/engine/
package engine

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Client talks to one Docker daemon.
type Client struct {
	http *http.Client
	dial func(ctx context.Context) (net.Conn, error)
}

// New connects to DOCKER_HOST-style addresses: unix:///var/run/docker.sock or tcp://host:2375.
func New(host string) (*Client, error) {
	u, err := url.Parse(host)
	if err != nil {
		return nil, fmt.Errorf("docker host %q: %w", host, err)
	}
	var network, address string
	switch u.Scheme {
	case "unix":
		network, address = "unix", u.Path
	case "tcp", "http":
		network, address = "tcp", u.Host
	default:
		return nil, fmt.Errorf("docker host %q: only unix:// and tcp:// are supported", host)
	}

	dialer := &net.Dialer{Timeout: 10 * time.Second}
	dial := func(ctx context.Context) (net.Conn, error) { return dialer.DialContext(ctx, network, address) }
	transport := &http.Transport{
		// Every request goes to the same daemon, whatever the URL says.
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) { return dial(ctx) },
	}
	return &Client{http: &http.Client{Transport: transport}, dial: dial}, nil
}

// Error is an error response from the daemon.
type Error struct {
	Status  int
	Message string
}

func (e *Error) Error() string { return fmt.Sprintf("docker: %s (%d)", e.Message, e.Status) }

// IsNotFound reports whether err is Docker saying "no such container/volume/image".
func IsNotFound(err error) bool {
	var e *Error
	return errors.As(err, &e) && e.Status == http.StatusNotFound
}

// IsConflict reports whether err is Docker saying the thing already exists or is in use.
func IsConflict(err error) bool {
	var e *Error
	return errors.As(err, &e) && e.Status == http.StatusConflict
}

// request builds a request to the daemon. The host part of the URL is ignored by the transport.
func request(ctx context.Context, method, path string, query url.Values, body io.Reader) (*http.Request, error) {
	target := "http://docker" + path
	if len(query) > 0 {
		target += "?" + query.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, method, target, body)
	if err != nil {
		return nil, err
	}
	return req, nil
}

// do sends a request and turns 4xx/5xx answers into *Error. The caller closes the body.
func (c *Client) do(ctx context.Context, method, path string, query url.Values, body io.Reader, contentType string) (*http.Response, error) {
	req, err := request(ctx, method, path, query, body)
	if err != nil {
		return nil, err
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("docker unreachable: %w", err)
	}
	if resp.StatusCode >= 400 {
		defer resp.Body.Close()
		return nil, readError(resp)
	}
	return resp, nil
}

func readError(resp *http.Response) error {
	var payload struct {
		Message string `json:"message"`
	}
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	if json.Unmarshal(data, &payload) != nil || payload.Message == "" {
		payload.Message = strings.TrimSpace(string(data))
	}
	return &Error{Status: resp.StatusCode, Message: payload.Message}
}

// call sends `in` as JSON (if not nil) and decodes the answer into `out` (if not nil).
func (c *Client) call(ctx context.Context, method, path string, query url.Values, in, out any) error {
	var body io.Reader
	contentType := ""
	if in != nil {
		data, err := json.Marshal(in)
		if err != nil {
			return err
		}
		body, contentType = bytes.NewReader(data), "application/json"
	}
	resp, err := c.do(ctx, method, path, query, body, contentType)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if out == nil {
		_, err = io.Copy(io.Discard, resp.Body)
		return err
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// hijack starts a request whose connection then becomes a raw two-way stream: container stdin
// and exec output. Docker answers "101 Switching Protocols" and stops speaking HTTP.
func (c *Client) hijack(ctx context.Context, path string, query url.Values, in any) (*Stream, error) {
	data, err := json.Marshal(in)
	if err != nil {
		return nil, err
	}
	conn, err := c.dial(ctx)
	if err != nil {
		return nil, fmt.Errorf("docker unreachable: %w", err)
	}
	req, err := request(ctx, http.MethodPost, path, query, bytes.NewReader(data))
	if err != nil {
		conn.Close()
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Connection", "Upgrade")
	req.Header.Set("Upgrade", "tcp")
	if err := req.Write(conn); err != nil {
		conn.Close()
		return nil, err
	}

	reader := bufio.NewReader(conn)
	resp, err := http.ReadResponse(reader, req)
	if err != nil {
		conn.Close()
		return nil, err
	}
	if resp.StatusCode != http.StatusSwitchingProtocols && resp.StatusCode != http.StatusOK {
		defer conn.Close()
		return nil, readError(resp)
	}
	return &Stream{conn: conn, reader: reader}, nil
}

// Stream is a hijacked connection. Reads may start with bytes already buffered while parsing
// the HTTP response, so they go through the buffered reader.
type Stream struct {
	conn   net.Conn
	reader *bufio.Reader
}

func (s *Stream) Read(p []byte) (int, error)  { return s.reader.Read(p) }
func (s *Stream) Write(p []byte) (int, error) { return s.conn.Write(p) }
func (s *Stream) Close() error                { return s.conn.Close() }

// CloseWrite tells the other side we're done sending, while still reading its answer.
func (s *Stream) CloseWrite() error {
	if cw, ok := s.conn.(interface{ CloseWrite() error }); ok {
		return cw.CloseWrite()
	}
	return nil
}
