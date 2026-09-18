package engine

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
)

// --- containers ------------------------------------------------------------

// ContainerSummary is one entry of GET /containers/json.
type ContainerSummary struct {
	ID     string            `json:"Id"`
	State  string            `json:"State"`  // "running", "exited", ...
	Status string            `json:"Status"` // "Up 5 minutes (healthy)"
	Labels map[string]string `json:"Labels"`
	Ports  []struct {
		PublicPort int    `json:"PublicPort"`
		Type       string `json:"Type"`
	} `json:"Ports"`
}

// ContainerInfo is the part of GET /containers/{id}/json the agent reads.
type ContainerInfo struct {
	ID    string `json:"Id"`
	State struct {
		Status    string `json:"Status"`
		Running   bool   `json:"Running"`
		StartedAt string `json:"StartedAt"`
		ExitCode  int    `json:"ExitCode"`
		OOMKilled bool   `json:"OOMKilled"`
		Health    *struct {
			Status string `json:"Status"`
		} `json:"Health"`
	} `json:"State"`
}

// ContainerList lists containers, including stopped ones, that have all the given labels ("key=value").
func (c *Client) ContainerList(ctx context.Context, labels ...string) ([]ContainerSummary, error) {
	query := url.Values{"all": {"true"}}
	if len(labels) > 0 {
		filters, _ := json.Marshal(map[string][]string{"label": labels})
		query.Set("filters", string(filters))
	}
	var out []ContainerSummary
	return out, c.call(ctx, http.MethodGet, "/containers/json", query, nil, &out)
}

// ContainerStatsRaw is the part of Docker's one-shot stats the panel shows.
type ContainerStatsRaw struct {
	CPUStats struct {
		CPUUsage struct {
			TotalUsage int64 `json:"total_usage"`
		} `json:"cpu_usage"`
		SystemUsage int64 `json:"system_cpu_usage"`
		OnlineCPUs  int   `json:"online_cpus"`
	} `json:"cpu_stats"`
	PreCPUStats struct {
		CPUUsage struct {
			TotalUsage int64 `json:"total_usage"`
		} `json:"cpu_usage"`
		SystemUsage int64 `json:"system_cpu_usage"`
	} `json:"precpu_stats"`
	MemoryStats struct {
		Usage int64            `json:"usage"`
		Limit int64            `json:"limit"`
		Stats map[string]int64 `json:"stats"`
	} `json:"memory_stats"`
}

// ContainerStats reads one snapshot of a container's CPU and memory use.
func (c *Client) ContainerStats(ctx context.Context, name string) (*ContainerStatsRaw, error) {
	var out ContainerStatsRaw
	query := url.Values{"stream": {"false"}, "one-shot": {"false"}}
	err := c.call(ctx, http.MethodGet, "/containers/"+url.PathEscape(name)+"/stats", query, nil, &out)
	if err != nil {
		return nil, err
	}
	return &out, nil
}

func (c *Client) ContainerInspect(ctx context.Context, name string) (*ContainerInfo, error) {
	var out ContainerInfo
	if err := c.call(ctx, http.MethodGet, "/containers/"+url.PathEscape(name)+"/json", nil, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ContainerCreate creates a container from a config shaped like the API's ContainerCreate body.
func (c *Client) ContainerCreate(ctx context.Context, name string, config any) (string, error) {
	var out struct {
		ID string `json:"Id"`
	}
	err := c.call(ctx, http.MethodPost, "/containers/create", url.Values{"name": {name}}, config, &out)
	return out.ID, err
}

func (c *Client) ContainerStart(ctx context.Context, name string) error {
	return c.call(ctx, http.MethodPost, "/containers/"+url.PathEscape(name)+"/start", nil, nil, nil)
}

// ContainerStop sends SIGTERM, then SIGKILL after timeout seconds.
func (c *Client) ContainerStop(ctx context.Context, name string, timeout int) error {
	query := url.Values{"t": {strconv.Itoa(timeout)}}
	return c.call(ctx, http.MethodPost, "/containers/"+url.PathEscape(name)+"/stop", query, nil, nil)
}

// ContainerUpdate changes a container's resources or restart policy without recreating it.
func (c *Client) ContainerUpdate(ctx context.Context, name string, update any) error {
	return c.call(ctx, http.MethodPost, "/containers/"+url.PathEscape(name)+"/update", nil, update, nil)
}

// ContainerRemove removes a container, running or not. A missing container is not an error.
func (c *Client) ContainerRemove(ctx context.Context, name string) error {
	query := url.Values{"force": {"true"}}
	err := c.call(ctx, http.MethodDelete, "/containers/"+url.PathEscape(name), query, nil, nil)
	if IsNotFound(err) {
		return nil
	}
	return err
}

// ContainerWait blocks until the container exits and returns its exit code.
func (c *Client) ContainerWait(ctx context.Context, name string) (int, error) {
	var out struct {
		StatusCode int `json:"StatusCode"`
	}
	err := c.call(ctx, http.MethodPost, "/containers/"+url.PathEscape(name)+"/wait", nil, nil, &out)
	return out.StatusCode, err
}

// ContainerLogs returns the multiplexed log stream (see Demux). With follow it ends when the container stops.
// With timestamps every line starts with the time Docker received it (RFC 3339) and a space.
func (c *Client) ContainerLogs(ctx context.Context, name string, follow bool, tail string, since int64, timestamps bool) (io.ReadCloser, error) {
	query := url.Values{
		"stdout":     {"true"},
		"stderr":     {"true"},
		"follow":     {strconv.FormatBool(follow)},
		"tail":       {tail},
		"since":      {strconv.FormatInt(since, 10)},
		"timestamps": {strconv.FormatBool(timestamps)},
	}
	resp, err := c.do(ctx, http.MethodGet, "/containers/"+url.PathEscape(name)+"/logs", query, nil, "")
	if err != nil {
		return nil, err
	}
	return resp.Body, nil
}

// ContainerAttachStdin opens the container's stdin, e.g. to type a command into a game console.
func (c *Client) ContainerAttachStdin(ctx context.Context, name string) (*Stream, error) {
	query := url.Values{"stream": {"true"}, "stdin": {"true"}}
	return c.hijack(ctx, "/containers/"+url.PathEscape(name)+"/attach", query, nil)
}

// ContainerArchive returns a tar of path inside the container (works on stopped containers too).
func (c *Client) ContainerArchive(ctx context.Context, name, path string) (io.ReadCloser, error) {
	resp, err := c.do(ctx, http.MethodGet, "/containers/"+url.PathEscape(name)+"/archive", url.Values{"path": {path}}, nil, "")
	if err != nil {
		return nil, err
	}
	return resp.Body, nil
}

// ContainerExtract unpacks a tar (plain or gzip) into dir inside the container, keeping owners.
func (c *Client) ContainerExtract(ctx context.Context, name, dir string, archive io.Reader) error {
	resp, err := c.do(ctx, http.MethodPut, "/containers/"+url.PathEscape(name)+"/archive", url.Values{"path": {dir}}, archive, "application/x-tar")
	if err != nil {
		return err
	}
	resp.Body.Close()
	return nil
}

// --- exec ------------------------------------------------------------------

// Exec runs cmd (an argv, never a shell string) in a running container and returns its output stream.
// Read it with Demux to the end, then ask ExecExitCode.
func (c *Client) Exec(ctx context.Context, container string, cmd []string) (id string, output *Stream, err error) {
	var created struct {
		ID string `json:"Id"`
	}
	body := map[string]any{"Cmd": cmd, "AttachStdout": true, "AttachStderr": true}
	if err := c.call(ctx, http.MethodPost, "/containers/"+url.PathEscape(container)+"/exec", nil, body, &created); err != nil {
		return "", nil, err
	}
	stream, err := c.hijack(ctx, "/exec/"+created.ID+"/start", nil, map[string]any{"Detach": false, "Tty": false})
	return created.ID, stream, err
}

func (c *Client) ExecExitCode(ctx context.Context, id string) (int, error) {
	var out struct {
		ExitCode int  `json:"ExitCode"`
		Running  bool `json:"Running"`
	}
	if err := c.call(ctx, http.MethodGet, "/exec/"+id+"/json", nil, nil, &out); err != nil {
		return 0, err
	}
	if out.Running {
		return 0, fmt.Errorf("exec %s is still running", id)
	}
	return out.ExitCode, nil
}

// --- volumes and images ------------------------------------------------------

func (c *Client) VolumeCreate(ctx context.Context, name string, labels map[string]string) error {
	return c.call(ctx, http.MethodPost, "/volumes/create", nil, map[string]any{"Name": name, "Labels": labels}, nil)
}

// VolumeExists reports whether a volume exists.
func (c *Client) VolumeExists(ctx context.Context, name string) (bool, error) {
	err := c.call(ctx, http.MethodGet, "/volumes/"+url.PathEscape(name), nil, nil, nil)
	if IsNotFound(err) {
		return false, nil
	}
	return err == nil, err
}

// VolumeRemove removes a volume. A missing volume is not an error.
func (c *Client) VolumeRemove(ctx context.Context, name string) error {
	err := c.call(ctx, http.MethodDelete, "/volumes/"+url.PathEscape(name), nil, nil, nil)
	if IsNotFound(err) {
		return nil
	}
	return err
}

// ImageInspect reports whether the image exists locally.
func (c *Client) ImageExists(ctx context.Context, image string) (bool, error) {
	err := c.call(ctx, http.MethodGet, "/images/"+image+"/json", nil, nil, nil)
	if IsNotFound(err) {
		return false, nil
	}
	return err == nil, err
}

// PullEvent is one progress message of an image pull.
type PullEvent struct {
	Status string `json:"status"`
	Error  string `json:"error"`
}

// ImagePull downloads image ("name:tag"), calling onEvent for each progress message.
func (c *Client) ImagePull(ctx context.Context, image string, onEvent func(PullEvent)) error {
	name, tag := splitImage(image)
	resp, err := c.do(ctx, http.MethodPost, "/images/create", url.Values{"fromImage": {name}, "tag": {tag}}, nil, "")
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 64<<10), 1<<20)
	for scanner.Scan() {
		var event PullEvent
		if json.Unmarshal(scanner.Bytes(), &event) != nil {
			continue
		}
		if event.Error != "" {
			return fmt.Errorf("pulling %s: %s", image, event.Error)
		}
		onEvent(event)
	}
	return scanner.Err()
}

// splitImage splits "registry:5000/name:tag" into name and tag ("latest" if none).
func splitImage(image string) (string, string) {
	slash := strings.LastIndex(image, "/")
	if colon := strings.LastIndex(image, ":"); colon > slash {
		return image[:colon], image[colon+1:]
	}
	return image, "latest"
}
