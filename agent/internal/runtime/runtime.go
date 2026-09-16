// Package runtime runs game servers as Docker containers from specs the panel sends.
// It knows nothing about games: images, env, ports and mounts all come from the panel.
package runtime

import (
	"archive/tar"
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"path"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/omka-1337/PossumDocker/agent/internal/engine"
)

// Labels mark what the agent owns, so it never touches other containers on the host.
const (
	LabelManaged = "possum.managed"
	LabelServer  = "possum.server_id"
	LabelRole    = "possum.role"
)

var validID = regexp.MustCompile(`^[A-Za-z0-9-]{1,64}$`)

// ValidID reports whether id is safe to put into container and volume names.
func ValidID(id string) bool { return validID.MatchString(id) }

func ContainerName(serverID string) string { return "possum-" + serverID }
func VolumeName(serverID string) string    { return "possum-" + serverID + "-data" }

func labels(serverID, role string) map[string]string {
	return map[string]string{LabelManaged: "true", LabelServer: serverID, LabelRole: role}
}

// PortBinding publishes a container port on the host.
type PortBinding struct {
	Host      int    `json:"host"`
	Container int    `json:"container"`
	Protocol  string `json:"protocol"` // "tcp" or "udp"
}

// Mount puts a folder of the server's volume at a path in the container.
type Mount struct {
	Subpath string `json:"subpath"`
	Path    string `json:"path"`
}

// ContainerSpec is what the panel renders from a game template (panel/app/runtime/spec.py).
type ContainerSpec struct {
	Image string `json:"image"`
	// nil keeps the image's own entrypoint/command; an empty list clears it.
	Entrypoint []string          `json:"entrypoint"`
	Command    []string          `json:"command"`
	Env        map[string]string `json:"env"`
	Ports      []PortBinding     `json:"ports"`
	DataPath   string            `json:"data_path"`
	Mounts     []Mount           `json:"mounts"`
	// Limits; 0 or absent: no limit.
	MemoryMB int     `json:"memory_mb"`
	CPUs     float64 `json:"cpus"`
	// Install script, run with sh. Copied into the container, never bind-mounted.
	Script []byte `json:"script"`
}

// Validate checks the paths: absolute, clean container paths, and volume subpaths that stay inside it.
// A ":" or "," would let a path smuggle extra options into Docker's mount syntax.
func (spec ContainerSpec) Validate() error {
	if spec.Image == "" {
		return errors.New("image is required")
	}
	if !cleanAbsolute(spec.DataPath) {
		return fmt.Errorf("invalid data_path %q", spec.DataPath)
	}
	for _, m := range spec.Mounts {
		if !cleanAbsolute(m.Path) {
			return fmt.Errorf("invalid mount path %q", m.Path)
		}
		if m.Subpath == "" || path.IsAbs(m.Subpath) || path.Clean(m.Subpath) != m.Subpath ||
			m.Subpath == ".." || strings.HasPrefix(m.Subpath, "../") || strings.ContainsAny(m.Subpath, ":,") {
			return fmt.Errorf("invalid mount subpath %q", m.Subpath)
		}
	}
	if spec.MemoryMB < 0 || spec.CPUs < 0 {
		return errors.New("limits can't be negative")
	}
	return nil
}

func cleanAbsolute(p string) bool {
	return path.IsAbs(p) && path.Clean(p) == p && p != "/" && !strings.ContainsAny(p, ":,")
}

// State is a container's state as the panel sees it.
type State struct {
	Status string `json:"status"`           // "running", "exited", "created", ...
	Health string `json:"health,omitempty"` // "starting", "healthy", "unhealthy" when the image has a healthcheck
	// How the last run ended, once it has.
	ExitCode  *int `json:"exit_code,omitempty"`
	OOMKilled bool `json:"oom_killed,omitempty"`
}

const (
	// Docker brings a crashed server back this many times before giving up.
	RestartAttempts = 3
	// A runaway plugin can't fork-bomb the host.
	PidsLimit = 4096
)

// hostLimits are the crash restarts and resource limits of a runtime container.
// Keep in step with host_limits in panel/app/runtime/docker.py.
func hostLimits(spec ContainerSpec) map[string]any {
	limits := map[string]any{
		// Only a non-zero exit is restarted; the panel switches this off before stopping a server.
		"RestartPolicy": map[string]any{"Name": "on-failure", "MaximumRetryCount": RestartAttempts},
		"PidsLimit":     PidsLimit,
	}
	if spec.MemoryMB > 0 {
		// Same limit for memory+swap: past it the game is killed instead of swapping the host to a crawl.
		bytes := int64(spec.MemoryMB) * 1024 * 1024
		limits["Memory"], limits["MemorySwap"] = bytes, bytes
	}
	if spec.CPUs > 0 {
		limits["NanoCpus"] = int64(spec.CPUs * 1e9)
	}
	return limits
}

// Dirs creates folders inside a server's volume. Implemented by the files helper.
type Dirs interface {
	EnsureDirs(ctx context.Context, serverID string, paths []string) error
}

type Runtime struct {
	docker *engine.Client
	dirs   Dirs
	log    *slog.Logger
}

func New(docker *engine.Client, dirs Dirs, log *slog.Logger) *Runtime {
	return &Runtime{docker: docker, dirs: dirs, log: log}
}

// --- queries ---------------------------------------------------------------

var (
	healthInStatus = regexp.MustCompile(`\((?:health: )?(starting|healthy|unhealthy)\)`)
	exitInStatus   = regexp.MustCompile(`^Exited \((-?\d+)\)`)
)

// States returns the state of every game server container, keyed by server id, in one Docker call.
func (r *Runtime) States(ctx context.Context) (map[string]State, error) {
	containers, err := r.docker.ContainerList(ctx, LabelManaged+"=true", LabelRole+"=runtime")
	if err != nil {
		return nil, err
	}
	states := make(map[string]State, len(containers))
	for _, c := range containers {
		id := c.Labels[LabelServer]
		if id == "" {
			continue
		}
		states[id] = listedState(c.State, c.Status)
	}
	return states, nil
}

// listedState reads what the list call has only as text: "Up 5 minutes (healthy)", "Exited (137) 2 minutes ago".
func listedState(status, text string) State {
	state := State{Status: status}
	if m := healthInStatus.FindStringSubmatch(text); m != nil {
		state.Health = m[1]
	}
	if m := exitInStatus.FindStringSubmatch(text); m != nil {
		code, _ := strconv.Atoi(m[1])
		state.ExitCode = &code
	}
	return state
}

// State of one server's container; nil when it doesn't exist.
func (r *Runtime) State(ctx context.Context, serverID string) (*State, error) {
	info, err := r.docker.ContainerInspect(ctx, ContainerName(serverID))
	if engine.IsNotFound(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	state := &State{Status: info.State.Status}
	if info.State.Health != nil {
		state.Health = info.State.Health.Status
	}
	if info.State.Status == "exited" || info.State.Status == "dead" {
		code := info.State.ExitCode
		state.ExitCode = &code
		state.OOMKilled = info.State.OOMKilled
	}
	return state, nil
}

// PublishedPorts are host ports any container publishes, "25565/tcp" style pairs.
func (r *Runtime) PublishedPorts(ctx context.Context) ([][2]any, error) {
	containers, err := r.docker.ContainerList(ctx)
	if err != nil {
		return nil, err
	}
	ports := [][2]any{}
	for _, c := range containers {
		for _, p := range c.Ports {
			if p.PublicPort > 0 {
				ports = append(ports, [2]any{p.PublicPort, p.Type})
			}
		}
	}
	return ports, nil
}

// --- install -----------------------------------------------------------------

// Pull downloads an image, reporting progress lines.
func (r *Runtime) Pull(ctx context.Context, image string, logLine func(string)) error {
	logLine("pulling " + image)
	layers := 0
	err := r.docker.ImagePull(ctx, image, func(e engine.PullEvent) {
		switch {
		// Per-layer progress is too chatty for a log; count finished layers instead.
		case e.Status == "Pull complete" || e.Status == "Already exists":
			layers++
		case strings.HasPrefix(e.Status, "Digest:") || strings.HasPrefix(e.Status, "Status:"):
			logLine(e.Status)
		}
	})
	if err != nil {
		return err
	}
	logLine(fmt.Sprintf("%s: %d layer(s) ready", image, layers))
	return nil
}

func (r *Runtime) EnsureVolume(ctx context.Context, serverID string) error {
	return r.docker.VolumeCreate(ctx, VolumeName(serverID), labels(serverID, "data"))
}

// RunInstall runs the install container to the end, streaming its output. A non-zero exit is an error.
func (r *Runtime) RunInstall(ctx context.Context, serverID string, spec ContainerSpec, logLine func(string)) error {
	name := ContainerName(serverID) + "-install"
	if err := r.docker.ContainerRemove(ctx, name); err != nil {
		return err
	}
	config := baseConfig(spec, serverID, "install")
	config["WorkingDir"] = spec.DataPath
	config["HostConfig"] = map[string]any{"Binds": []string{VolumeName(serverID) + ":" + spec.DataPath}}
	if _, err := r.docker.ContainerCreate(ctx, name, config); err != nil {
		return err
	}
	// Whatever happens, don't leave the install container behind (with a detached context:
	// the request may be what got cancelled).
	defer r.docker.ContainerRemove(context.WithoutCancel(ctx), name) //nolint:errcheck

	if len(spec.Script) > 0 {
		archive, err := tarWith("possum/install.sh", spec.Script, 0o755)
		if err != nil {
			return err
		}
		if err := r.docker.ContainerExtract(ctx, name, "/", archive); err != nil {
			return fmt.Errorf("copying the install script: %w", err)
		}
	}
	if err := r.docker.ContainerStart(ctx, name); err != nil {
		return err
	}
	if err := r.followLines(ctx, name, "all", 0, logLine); err != nil {
		return err
	}
	code, err := r.docker.ContainerWait(ctx, name)
	if err != nil {
		return err
	}
	if code != 0 {
		return fmt.Errorf("install step exited with code %d", code)
	}
	return nil
}

// Create (re)creates the runtime container. The data lives in the volume, so this loses nothing.
func (r *Runtime) Create(ctx context.Context, serverID string, spec ContainerSpec) error {
	name := ContainerName(serverID)
	if err := r.docker.ContainerRemove(ctx, name); err != nil {
		return err
	}
	if len(spec.Mounts) > 0 {
		// Docker refuses to mount a volume subpath that doesn't exist yet.
		subpaths := make([]string, len(spec.Mounts))
		for i, m := range spec.Mounts {
			subpaths[i] = m.Subpath
		}
		if err := r.dirs.EnsureDirs(ctx, serverID, subpaths); err != nil {
			return err
		}
	}

	exposed := map[string]any{}
	bindings := map[string]any{}
	for _, p := range spec.Ports {
		key := strconv.Itoa(p.Container) + "/" + p.Protocol
		exposed[key] = map[string]any{}
		bindings[key] = []map[string]string{{"HostPort": strconv.Itoa(p.Host)}}
	}
	host := hostLimits(spec)
	host["PortBindings"] = bindings
	// A tiny init as PID 1 forwards SIGTERM to the game; a game running as PID 1 would ignore it.
	host["Init"] = true
	if len(spec.Mounts) == 0 {
		host["Binds"] = []string{VolumeName(serverID) + ":" + spec.DataPath}
	} else {
		mounts := make([]map[string]any, len(spec.Mounts))
		for i, m := range spec.Mounts {
			mounts[i] = map[string]any{
				"Type": "volume", "Source": VolumeName(serverID), "Target": m.Path,
				"VolumeOptions": map[string]any{"Subpath": m.Subpath},
			}
		}
		host["Mounts"] = mounts
	}

	config := baseConfig(spec, serverID, "runtime")
	// stdin stays open so the panel can type commands into the game console.
	config["OpenStdin"], config["StdinOnce"], config["Tty"] = true, false, false
	config["ExposedPorts"] = exposed
	config["HostConfig"] = host
	_, err := r.docker.ContainerCreate(ctx, name, config)
	return err
}

func baseConfig(spec ContainerSpec, serverID, role string) map[string]any {
	env := make([]string, 0, len(spec.Env))
	for k, v := range spec.Env {
		env = append(env, k+"="+v)
	}
	config := map[string]any{"Image": spec.Image, "Env": env, "Labels": labels(serverID, role)}
	if spec.Entrypoint != nil {
		config["Entrypoint"] = spec.Entrypoint
	}
	if spec.Command != nil {
		config["Cmd"] = spec.Command
	}
	return config
}

// --- lifecycle ---------------------------------------------------------------

func (r *Runtime) Start(ctx context.Context, serverID string) error {
	return r.docker.ContainerStart(ctx, ContainerName(serverID))
}

// Stop stops gracefully: the game's own stop command (if any), then SIGTERM, then SIGKILL.
func (r *Runtime) Stop(ctx context.Context, serverID, command string, timeout int) error {
	name := ContainerName(serverID)
	// A game that exits with an error code on its way down must not be brought back by Docker.
	// The next start creates a new container with the policy back on.
	norestart := map[string]any{"RestartPolicy": map[string]any{"Name": "no"}}
	if err := r.docker.ContainerUpdate(ctx, name, norestart); err != nil {
		return err
	}
	if command != "" {
		if err := r.SendCommand(ctx, serverID, command); err != nil {
			return err
		}
		waitCtx, cancel := context.WithTimeout(ctx, time.Duration(timeout)*time.Second)
		_, err := r.docker.ContainerWait(waitCtx, name)
		cancel()
		if err == nil {
			return nil
		}
		r.log.Warn("server ignored its stop command, sending SIGTERM", "server", serverID, "command", command)
	}
	return r.docker.ContainerStop(ctx, name, timeout)
}

// SendCommand types one line into the game's console (its stdin).
func (r *Runtime) SendCommand(ctx context.Context, serverID, line string) error {
	stream, err := r.docker.ContainerAttachStdin(ctx, ContainerName(serverID))
	if err != nil {
		return err
	}
	defer stream.Close()
	if _, err := io.WriteString(stream, line+"\n"); err != nil {
		return err
	}
	// Ending our attach doesn't close the container's stdin (StdinOnce is false).
	return stream.CloseWrite()
}

// Logs streams console lines until the container stops. Nothing (and no error) if it doesn't exist.
func (r *Runtime) Logs(ctx context.Context, serverID, tail string, since int64, line func(string)) error {
	err := r.followLines(ctx, ContainerName(serverID), tail, since, line)
	if engine.IsNotFound(err) {
		return nil
	}
	return err
}

func (r *Runtime) followLines(ctx context.Context, name, tail string, since int64, line func(string)) error {
	body, err := r.docker.ContainerLogs(ctx, name, true, tail, since)
	if err != nil {
		return err
	}
	defer body.Close()

	// stdout and stderr interleaved into one pipe, read back line by line.
	reader, writer := io.Pipe()
	go func() { writer.CloseWithError(engine.Demux(body, writer, writer)) }()
	defer reader.Close()
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 64<<10), 1<<20)
	for scanner.Scan() {
		line(scanner.Text())
	}
	if err := scanner.Err(); err != nil && !errors.Is(err, context.Canceled) {
		return err
	}
	return nil
}

// ReadFile returns a file from the server container (running or not); nil when it doesn't exist.
func (r *Runtime) ReadFile(ctx context.Context, serverID, filePath string) ([]byte, error) {
	archive, err := r.docker.ContainerArchive(ctx, ContainerName(serverID), filePath)
	if engine.IsNotFound(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer archive.Close()
	tr := tar.NewReader(archive)
	for {
		header, err := tr.Next()
		if err == io.EOF {
			return nil, nil
		}
		if err != nil {
			return nil, err
		}
		if header.Typeflag == tar.TypeReg {
			return io.ReadAll(tr)
		}
	}
}

// WriteFile replaces a file in the server container, keeping its owner and mode.
func (r *Runtime) WriteFile(ctx context.Context, serverID, filePath string, data []byte) error {
	name := ContainerName(serverID)
	header := &tar.Header{Name: path.Base(filePath), Mode: 0o644, Size: int64(len(data)), ModTime: time.Now()}
	if old, err := r.docker.ContainerArchive(ctx, name, filePath); err == nil {
		if h, err := tar.NewReader(old).Next(); err == nil {
			header.Mode, header.Uid, header.Gid = h.Mode, h.Uid, h.Gid
		}
		old.Close()
	} else if !engine.IsNotFound(err) {
		return err
	}
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	if err := tw.WriteHeader(header); err != nil {
		return err
	}
	if _, err := tw.Write(data); err != nil {
		return err
	}
	if err := tw.Close(); err != nil {
		return err
	}
	return r.docker.ContainerExtract(ctx, name, path.Dir(filePath), &buf)
}

// Remove deletes a server's containers and its volume. Irreversible.
func (r *Runtime) Remove(ctx context.Context, serverID string, closeFiles func(context.Context, string) error) error {
	// The file helper keeps the volume in use.
	if err := closeFiles(ctx, serverID); err != nil {
		return err
	}
	name := ContainerName(serverID)
	for _, n := range []string{name + "-install", name} {
		if err := r.docker.ContainerRemove(ctx, n); err != nil {
			return err
		}
	}
	return r.docker.VolumeRemove(ctx, VolumeName(serverID))
}

// tarWith builds a tar holding one file (and its folder), for copying files into containers.
func tarWith(name string, data []byte, mode int64) (io.Reader, error) {
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	if dir := path.Dir(name); dir != "." {
		if err := tw.WriteHeader(&tar.Header{Name: dir + "/", Typeflag: tar.TypeDir, Mode: 0o755}); err != nil {
			return nil, err
		}
	}
	if err := tw.WriteHeader(&tar.Header{Name: name, Mode: mode, Size: int64(len(data)), ModTime: time.Now()}); err != nil {
		return nil, err
	}
	if _, err := tw.Write(data); err != nil {
		return nil, err
	}
	if err := tw.Close(); err != nil {
		return nil, err
	}
	return &buf, nil
}
