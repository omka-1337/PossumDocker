// Package files works on a game server's data volume through a helper container.
//
// The game container may be stopped, and reading the volume on the host would need root.
// So file operations run in a tiny busybox container that mounts the volume at /data: no network,
// started on demand, removed after a few idle minutes. A symlink in the volume pointing "outside"
// leads into the empty helper filesystem, never onto the host.
package files

import (
	"archive/tar"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"path"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/omka-1337/possum/agent/internal/engine"
	"github.com/omka-1337/possum/agent/internal/runtime"
)

const (
	HelperImage  = "busybox:stable"
	Root         = "/data"
	IdleAfter    = 10 * time.Minute
	MaxTextBytes = 2 << 20
)

// Error is a problem the user caused or can fix (not found, already exists...), with an HTTP status.
type Error struct {
	Status  int
	Message string
}

func (e *Error) Error() string { return e.Message }

func newError(status int, format string, args ...any) *Error {
	return &Error{Status: status, Message: fmt.Sprintf(format, args...)}
}

// Entry is one file or folder.
type Entry struct {
	Name  string `json:"name"`
	Type  string `json:"type"` // "file", "dir", "symlink" or "other"
	Size  int64  `json:"size"`
	Mtime int64  `json:"mtime"`
}

type Files struct {
	docker *engine.Client
	log    *slog.Logger

	mu       sync.Mutex
	locks    map[string]*sync.Mutex // one per server: two requests must not both create its helper
	lastUsed map[string]time.Time
}

func New(docker *engine.Client, log *slog.Logger) *Files {
	return &Files{docker: docker, log: log, locks: map[string]*sync.Mutex{}, lastUsed: map[string]time.Time{}}
}

// Resolve normalises a path inside the volume: "/a/../b", "b/", "../../b" all become "b"; the root is "".
func Resolve(p string) string {
	clean := strings.TrimPrefix(path.Clean("/"+p), "/")
	return clean
}

func abs(p string) string {
	if rel := Resolve(p); rel != "" {
		return Root + "/" + rel
	}
	return Root
}

// ValidName reports whether name is a single, sensible file name.
func ValidName(name string) bool {
	return name != "" && name != "." && name != ".." && len(name) <= 255 && !strings.ContainsAny(name, "/\x00")
}

// --- helper container ---------------------------------------------------------

func helperName(serverID string) string { return runtime.ContainerName(serverID) + "-files" }

func (f *Files) serverLock(serverID string) *sync.Mutex {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.locks[serverID] == nil {
		f.locks[serverID] = &sync.Mutex{}
	}
	f.lastUsed[serverID] = time.Now()
	return f.locks[serverID]
}

func (f *Files) touch(serverID string) {
	f.mu.Lock()
	f.lastUsed[serverID] = time.Now()
	f.mu.Unlock()
}

// helper makes sure the server's helper container runs and returns its name.
func (f *Files) helper(ctx context.Context, serverID string) (string, error) {
	lock := f.serverLock(serverID)
	lock.Lock()
	defer lock.Unlock()

	name := helperName(serverID)
	info, err := f.docker.ContainerInspect(ctx, name)
	switch {
	case err == nil && info.State.Running:
		return name, nil
	case err == nil:
		if err := f.docker.ContainerRemove(ctx, name); err != nil {
			return "", err
		}
	case !engine.IsNotFound(err):
		return "", err
	}
	// Docker would quietly create a missing volume for the bind: never for a server that doesn't exist.
	if exists, err := f.docker.VolumeExists(ctx, runtime.VolumeName(serverID)); err != nil {
		return "", err
	} else if !exists {
		return "", newError(http.StatusNotFound, "this server has no files")
	}

	config := map[string]any{
		"Image":  HelperImage,
		"Cmd":    []string{"sleep", "infinity"},
		"Labels": map[string]string{runtime.LabelManaged: "true", runtime.LabelServer: serverID, runtime.LabelRole: "files"},
		"HostConfig": map[string]any{
			"Binds":       []string{runtime.VolumeName(serverID) + ":" + Root},
			"NetworkMode": "none",
			"AutoRemove":  true,
			"Init":        true,
		},
	}
	_, err = f.docker.ContainerCreate(ctx, name, config)
	if engine.IsNotFound(err) { // the image isn't here yet
		if err := f.docker.ImagePull(ctx, HelperImage, func(engine.PullEvent) {}); err != nil {
			return "", err
		}
		_, err = f.docker.ContainerCreate(ctx, name, config)
	}
	if err != nil {
		return "", err
	}
	return name, f.docker.ContainerStart(ctx, name)
}

// Close removes the server's helper container.
func (f *Files) Close(ctx context.Context, serverID string) error {
	f.mu.Lock()
	delete(f.lastUsed, serverID)
	f.mu.Unlock()
	return f.docker.ContainerRemove(ctx, helperName(serverID))
}

// ReapIdle removes helpers nobody used for a while. Run it in the background.
func (f *Files) ReapIdle(ctx context.Context) {
	ticker := time.NewTicker(time.Minute)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		f.mu.Lock()
		var idle []string
		for id, used := range f.lastUsed {
			if time.Since(used) > IdleAfter {
				idle = append(idle, id)
			}
		}
		f.mu.Unlock()
		for _, id := range idle {
			f.log.Info("stopping idle file helper", "server", id)
			if err := f.Close(ctx, id); err != nil {
				f.log.Error("stopping file helper", "server", id, "err", err)
			}
		}
	}
}

// run runs a command (argv, never a shell string) in the helper and returns its stdout.
// A non-zero exit becomes an *Error with the command's last line of stderr.
func (f *Files) run(ctx context.Context, serverID string, cmd ...string) ([]byte, error) {
	name, err := f.helper(ctx, serverID)
	if err != nil {
		return nil, err
	}
	id, stream, err := f.docker.Exec(ctx, name, cmd)
	if err != nil {
		return nil, err
	}
	var stdout, stderr bytes.Buffer
	err = engine.Demux(stream, &stdout, &stderr)
	stream.Close()
	if err != nil {
		return nil, err
	}
	code, err := f.docker.ExecExitCode(ctx, id)
	if err != nil {
		return nil, err
	}
	if code != 0 {
		lines := strings.Split(strings.TrimSpace(stderr.String()), "\n")
		message := lines[len(lines)-1]
		if message == "" {
			message = cmd[0] + " failed"
		}
		return nil, newError(http.StatusBadRequest, "%s", message)
	}
	return stdout.Bytes(), nil
}

// stream runs a command and copies its stdout to w. The exit code is checked at the end.
func (f *Files) stream(ctx context.Context, serverID string, w io.Writer, cmd ...string) error {
	name, err := f.helper(ctx, serverID)
	if err != nil {
		return err
	}
	id, stream, err := f.docker.Exec(ctx, name, cmd)
	if err != nil {
		return err
	}
	var stderr bytes.Buffer
	err = engine.Demux(stream, touchingWriter{w, func() { f.touch(serverID) }}, &stderr)
	stream.Close()
	if err != nil {
		return err
	}
	code, err := f.docker.ExecExitCode(ctx, id)
	if err != nil {
		return err
	}
	if code != 0 {
		return fmt.Errorf("%s exited with %d: %s", cmd[0], code, strings.TrimSpace(stderr.String()))
	}
	return nil
}

// touchingWriter marks the helper as in use while a long download or backup runs.
type touchingWriter struct {
	io.Writer
	touch func()
}

func (t touchingWriter) Write(p []byte) (int, error) {
	t.touch()
	return t.Writer.Write(p)
}

// --- queries ------------------------------------------------------------------

var kinds = map[string]string{
	"directory": "dir", "regular file": "file", "regular empty file": "file", "symbolic link": "symlink",
}

// SearchLimit is how many matches a search returns at most.
const SearchLimit = 500

// Match is a search result: an entry and where it is, relative to the volume.
type Match struct {
	Entry
	Path string `json:"path"`
}

// globEscape makes a user's text match literally in find's -iname.
func globEscape(text string) string {
	var b strings.Builder
	for _, r := range text {
		if strings.ContainsRune(`*?[]\`, r) {
			b.WriteRune('\\')
		}
		b.WriteRune(r)
	}
	return b.String()
}

// Search finds files and folders whose name contains query (ignoring case), anywhere under p.
// Past SearchLimit matches the rest are left out and truncated is true.
func (f *Files) Search(ctx context.Context, serverID, p, query string) (matches []Match, truncated bool, err error) {
	if _, err := f.require(ctx, serverID, p, "dir"); err != nil {
		return nil, false, err
	}
	out, err := f.run(ctx, serverID, "find", abs(p), "-mindepth", "1", "-iname", "*"+globEscape(query)+"*",
		"-exec", "stat", "-c", "%F|%s|%Y|%n", "{}", "+")
	if err != nil {
		return nil, false, err
	}
	matches = []Match{}
	for _, line := range strings.Split(string(out), "\n") {
		entry, ok := parseStat(line)
		if !ok {
			continue
		}
		if len(matches) == SearchLimit {
			return matches, true, nil
		}
		full := strings.SplitN(line, "|", 4)[3]
		matches = append(matches, Match{Entry: entry, Path: strings.TrimPrefix(full, Root+"/")})
	}
	return matches, false, nil
}

// parseStat reads a "%F|%s|%Y|%n" line. The name comes last, so a "|" inside it is fine.
func parseStat(line string) (Entry, bool) {
	parts := strings.SplitN(line, "|", 4)
	if len(parts) != 4 {
		return Entry{}, false
	}
	size, err1 := strconv.ParseInt(parts[1], 10, 64)
	mtime, err2 := strconv.ParseInt(parts[2], 10, 64)
	if err1 != nil || err2 != nil {
		return Entry{}, false
	}
	kind, ok := kinds[parts[0]]
	if !ok {
		kind = "other"
	}
	return Entry{Name: path.Base(parts[3]), Type: kind, Size: size, Mtime: mtime}, true
}

// Stat returns nil when the path doesn't exist.
func (f *Files) Stat(ctx context.Context, serverID, p string) (*Entry, error) {
	out, err := f.run(ctx, serverID, "stat", "-c", "%F|%s|%Y|%n", abs(p))
	var fe *Error
	if errors.As(err, &fe) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	entry, ok := parseStat(strings.TrimSpace(string(out)))
	if !ok {
		return nil, nil
	}
	return &entry, nil
}

func (f *Files) require(ctx context.Context, serverID, p, kind string) (*Entry, error) {
	entry, err := f.Stat(ctx, serverID, p)
	if err != nil {
		return nil, err
	}
	if entry == nil {
		name := Resolve(p)
		if name == "" {
			name = "/"
		}
		return nil, newError(http.StatusNotFound, "'%s' not found", name)
	}
	if kind != "" && entry.Type != kind {
		what := "file"
		if kind == "dir" {
			what = "folder"
		}
		return nil, newError(http.StatusBadRequest, "'%s' is not a %s", Resolve(p), what)
	}
	return entry, nil
}

func (f *Files) owner(ctx context.Context, serverID, p string) (string, error) {
	out, err := f.run(ctx, serverID, "stat", "-c", "%u:%g", abs(p))
	return strings.TrimSpace(string(out)), err
}

func (f *Files) List(ctx context.Context, serverID, p string) ([]Entry, error) {
	if _, err := f.require(ctx, serverID, p, "dir"); err != nil {
		return nil, err
	}
	out, err := f.run(ctx, serverID, "find", abs(p), "-mindepth", "1", "-maxdepth", "1",
		"-exec", "stat", "-c", "%F|%s|%Y|%n", "{}", "+")
	if err != nil {
		return nil, err
	}
	entries := []Entry{}
	for _, line := range strings.Split(string(out), "\n") {
		if entry, ok := parseStat(line); ok {
			entries = append(entries, entry)
		}
	}
	return entries, nil
}

// --- changes ------------------------------------------------------------------

func (f *Files) EnsureDirs(ctx context.Context, serverID string, paths []string) error {
	targets := []string{}
	for _, p := range paths {
		if Resolve(p) != "" {
			targets = append(targets, abs(p))
		}
	}
	if len(targets) == 0 {
		return nil
	}
	_, err := f.run(ctx, serverID, append([]string{"mkdir", "-p"}, targets...)...)
	return err
}

func (f *Files) Mkdir(ctx context.Context, serverID, p string) error {
	rel := Resolve(p)
	if rel == "" {
		return newError(http.StatusBadRequest, "invalid folder name")
	}
	parent := path.Dir(rel)
	if parent == "." {
		parent = ""
	}
	if _, err := f.require(ctx, serverID, parent, "dir"); err != nil {
		return err
	}
	if entry, err := f.Stat(ctx, serverID, rel); err != nil {
		return err
	} else if entry != nil {
		return newError(http.StatusConflict, "'%s' already exists", path.Base(rel))
	}
	if _, err := f.run(ctx, serverID, "mkdir", abs(rel)); err != nil {
		return err
	}
	// The helper runs as root; hand the folder to whoever owns the parent (the game's user).
	owner, err := f.owner(ctx, serverID, parent)
	if err != nil {
		return err
	}
	_, err = f.run(ctx, serverID, "chown", owner, abs(rel))
	return err
}

func (f *Files) Rename(ctx context.Context, serverID, p, newName string) error {
	rel := Resolve(p)
	if rel == "" {
		return newError(http.StatusBadRequest, "can't rename the root folder")
	}
	if !ValidName(newName) {
		return newError(http.StatusBadRequest, "invalid name")
	}
	target := path.Join(path.Dir(rel), newName)
	if _, err := f.require(ctx, serverID, rel, ""); err != nil {
		return err
	}
	if entry, err := f.Stat(ctx, serverID, target); err != nil {
		return err
	} else if entry != nil {
		return newError(http.StatusConflict, "'%s' already exists", newName)
	}
	_, err := f.run(ctx, serverID, "mv", "-n", abs(rel), abs(target))
	return err
}

// checkTransfer validates a move/copy and returns the sources that actually need moving.
func (f *Files) checkTransfer(ctx context.Context, serverID string, sources []string, destination string) ([]string, error) {
	dest := Resolve(destination)
	existing, err := f.List(ctx, serverID, dest)
	if err != nil {
		return nil, err
	}
	names := map[string]bool{}
	for _, e := range existing {
		names[e.Name] = true
	}
	var rels []string
	for _, source := range sources {
		rel := Resolve(source)
		parent := path.Dir(rel)
		if parent == "." {
			parent = ""
		}
		switch {
		case rel == "":
			return nil, newError(http.StatusBadRequest, "can't move the root folder")
		case dest == rel || strings.HasPrefix(dest, rel+"/"):
			return nil, newError(http.StatusBadRequest, "can't put '%s' inside itself", rel)
		case parent == dest:
			continue // already there
		case names[path.Base(rel)]:
			return nil, newError(http.StatusConflict, "'%s' already exists in the destination", path.Base(rel))
		}
		rels = append(rels, abs(rel))
	}
	return rels, nil
}

func (f *Files) Move(ctx context.Context, serverID string, sources []string, destination string) error {
	rels, err := f.checkTransfer(ctx, serverID, sources, destination)
	if err != nil || len(rels) == 0 {
		return err
	}
	_, err = f.run(ctx, serverID, append(append([]string{"mv", "-n"}, rels...), abs(destination))...)
	return err
}

func (f *Files) Copy(ctx context.Context, serverID string, sources []string, destination string) error {
	rels, err := f.checkTransfer(ctx, serverID, sources, destination)
	if err != nil || len(rels) == 0 {
		return err
	}
	_, err = f.run(ctx, serverID, append(append([]string{"cp", "-a", "-n"}, rels...), abs(destination))...)
	return err
}

func (f *Files) Delete(ctx context.Context, serverID string, paths []string) error {
	if len(paths) == 0 {
		return newError(http.StatusBadRequest, "nothing to delete")
	}
	targets := make([]string, 0, len(paths))
	for _, p := range paths {
		if Resolve(p) == "" {
			return newError(http.StatusBadRequest, "can't delete the root folder")
		}
		targets = append(targets, abs(p))
	}
	_, err := f.run(ctx, serverID, append([]string{"rm", "-rf"}, targets...)...)
	return err
}

// --- content ------------------------------------------------------------------

// ReadText returns a small text file. Files that aren't UTF-8 are read as Latin-1.
func (f *Files) ReadText(ctx context.Context, serverID, p string) (string, error) {
	entry, err := f.require(ctx, serverID, p, "file")
	if err != nil {
		return "", err
	}
	if entry.Size > MaxTextBytes {
		return "", newError(http.StatusRequestEntityTooLarge, "the file is too big to edit in the browser, download it instead")
	}
	data, err := f.run(ctx, serverID, "cat", abs(p))
	if err != nil {
		return "", err
	}
	if bytes.IndexByte(data, 0) >= 0 {
		return "", newError(http.StatusUnsupportedMediaType, "this doesn't look like a text file")
	}
	if utf8.Valid(data) {
		return string(data), nil
	}
	runes := make([]rune, len(data))
	for i, b := range data {
		runes[i] = rune(b)
	}
	return string(runes), nil
}

// WriteText replaces a file's content, keeping its owner and permissions.
func (f *Files) WriteText(ctx context.Context, serverID, p, content string) error {
	rel := Resolve(p)
	if len(content) > MaxTextBytes {
		return newError(http.StatusRequestEntityTooLarge, "the file is too big")
	}
	if _, err := f.require(ctx, serverID, rel, "file"); err != nil {
		return err
	}
	out, err := f.run(ctx, serverID, "stat", "-c", "%u %g %a", abs(rel))
	if err != nil {
		return err
	}
	var uid, gid int
	var mode string
	if _, err := fmt.Sscan(string(out), &uid, &gid, &mode); err != nil {
		return err
	}
	perm, _ := strconv.ParseInt(mode, 8, 64)

	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	header := &tar.Header{Name: path.Base(rel), Size: int64(len(content)), Mode: perm, Uid: uid, Gid: gid, ModTime: time.Now()}
	if err := tw.WriteHeader(header); err != nil {
		return err
	}
	if _, err := io.WriteString(tw, content); err != nil {
		return err
	}
	if err := tw.Close(); err != nil {
		return err
	}
	name, err := f.helper(ctx, serverID)
	if err != nil {
		return err
	}
	return f.docker.ContainerExtract(ctx, name, abs(path.Dir(rel)), &buf)
}

// Upload unpacks a tar the panel built from uploaded files into directory, handing every entry to
// the directory's owner. Entry names are checked, so nothing lands outside the directory.
func (f *Files) Upload(ctx context.Context, serverID, directory string, archive io.Reader) error {
	dir := Resolve(directory)
	if _, err := f.require(ctx, serverID, dir, "dir"); err != nil {
		return err
	}
	owner, err := f.owner(ctx, serverID, dir)
	if err != nil {
		return err
	}
	var uid, gid int
	if _, err := fmt.Sscanf(owner, "%d:%d", &uid, &gid); err != nil {
		return err
	}
	name, err := f.helper(ctx, serverID)
	if err != nil {
		return err
	}

	// Rewrite the tar on the fly while Docker reads it: no temporary copy of a big upload.
	reader, writer := io.Pipe()
	go func() { writer.CloseWithError(rewriteUpload(archive, writer, uid, gid)) }()
	err = f.docker.ContainerExtract(ctx, name, abs(dir), reader)
	reader.CloseWithError(err)
	var fe *Error
	if errors.As(err, &fe) {
		return fe
	}
	return err
}

func rewriteUpload(in io.Reader, out io.Writer, uid, gid int) error {
	tr, tw := tar.NewReader(in), tar.NewWriter(out)
	for {
		header, err := tr.Next()
		if err == io.EOF {
			return tw.Close()
		}
		if err != nil {
			return err
		}
		name := strings.TrimSuffix(header.Name, "/")
		for _, part := range strings.Split(name, "/") {
			if !ValidName(part) {
				return newError(http.StatusBadRequest, "invalid upload path '%s'", header.Name)
			}
		}
		if header.Typeflag != tar.TypeReg && header.Typeflag != tar.TypeDir {
			return newError(http.StatusBadRequest, "'%s': only files and folders can be uploaded", header.Name)
		}
		header.Uid, header.Gid, header.Uname, header.Gname = uid, gid, "", ""
		if header.Typeflag == tar.TypeDir {
			header.Mode = 0o755
		} else {
			header.Mode = 0o644
		}
		if err := tw.WriteHeader(header); err != nil {
			return err
		}
		if _, err := io.Copy(tw, tr); err != nil {
			return err
		}
	}
}

// Usage is how many bytes the paths take on disk; no paths: the whole volume. Something counted twice
// (a folder and a file inside it) counts once.
func (f *Files) Usage(ctx context.Context, serverID string, paths []string) (int64, error) {
	cmd := []string{"du", "-skc"}
	if len(paths) == 0 {
		cmd = append(cmd, Root)
	}
	for _, p := range paths {
		cmd = append(cmd, abs(p))
	}
	out, err := f.run(ctx, serverID, cmd...)
	if err != nil {
		return 0, err
	}
	return lastNumber(out, 1024)
}

// UnpackedSize is what a .zip says its files add up to. A crafted archive can lie; the caller checks
// the space actually used afterwards too.
func (f *Files) UnpackedSize(ctx context.Context, serverID, p string) (int64, error) {
	if _, err := f.require(ctx, serverID, Resolve(p), "file"); err != nil {
		return 0, err
	}
	// The last line of "unzip -l": "  5000002                     2 files"
	out, err := f.run(ctx, serverID, "unzip", "-l", abs(p))
	if err != nil {
		return 0, newError(http.StatusBadRequest, "not a readable .zip archive")
	}
	return lastNumber(out, 1)
}

// lastNumber reads the first field of the last line, times unit.
func lastNumber(out []byte, unit int64) (int64, error) {
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	fields := strings.Fields(lines[len(lines)-1])
	if len(fields) == 0 {
		return 0, fmt.Errorf("unexpected output %q", out)
	}
	n, err := strconv.ParseInt(fields[0], 10, 64)
	if err != nil {
		return 0, fmt.Errorf("unexpected output %q", out)
	}
	return n * unit, nil
}

// Extract unzips an archive next to it, into a folder named after it, and returns that folder.
func (f *Files) Extract(ctx context.Context, serverID, p string) (string, error) {
	rel := Resolve(p)
	if _, err := f.require(ctx, serverID, rel, "file"); err != nil {
		return "", err
	}
	base := path.Base(rel)
	if !strings.HasSuffix(strings.ToLower(base), ".zip") {
		return "", newError(http.StatusBadRequest, "only .zip archives can be extracted")
	}
	folder := strings.TrimSuffix(base, base[len(base)-4:])
	if folder == "" {
		folder = "archive"
	}
	parent := path.Dir(rel)
	if parent == "." {
		parent = ""
	}
	target := path.Join(parent, folder)
	if entry, err := f.Stat(ctx, serverID, target); err != nil {
		return "", err
	} else if entry != nil {
		return "", newError(http.StatusConflict, "'%s' already exists", folder)
	}
	if _, err := f.run(ctx, serverID, "mkdir", abs(target)); err != nil {
		return "", err
	}
	if _, err := f.run(ctx, serverID, "unzip", "-q", "-o", abs(rel), "-d", abs(target)); err != nil {
		f.run(ctx, serverID, "rm", "-rf", abs(target)) //nolint:errcheck
		return "", err
	}
	owner, err := f.owner(ctx, serverID, parent)
	if err != nil {
		return "", err
	}
	if _, err := f.run(ctx, serverID, "chown", "-R", owner, abs(target)); err != nil {
		return "", err
	}
	return target, nil
}

// Download streams one file.
func (f *Files) Download(ctx context.Context, serverID, p string, w io.Writer) error {
	return f.stream(ctx, serverID, w, "cat", abs(p))
}

// DownloadArchive streams names from directory as a .tar.gz.
func (f *Files) DownloadArchive(ctx context.Context, serverID, directory string, names []string, w io.Writer) error {
	cmd := []string{"tar", "-czf", "-", "-C", abs(directory)}
	for _, n := range names {
		if !ValidName(n) {
			return newError(http.StatusBadRequest, "invalid name '%s'", n)
		}
		// "./name": a file called "-rf" must stay a file name, not become a tar option.
		cmd = append(cmd, "./"+n)
	}
	return f.stream(ctx, serverID, w, cmd...)
}

// --- backups --------------------------------------------------------------------

// BackupPaths returns the paths a backup of `paths` will really hold (missing ones skipped);
// nil means the whole volume.
func (f *Files) BackupPaths(ctx context.Context, serverID string, paths []string) ([]string, error) {
	if paths == nil {
		return nil, nil
	}
	present := []string{}
	seen := map[string]bool{}
	for _, p := range paths {
		rel := Resolve(p)
		if rel == "" || seen[rel] {
			continue
		}
		seen[rel] = true
		entry, err := f.Stat(ctx, serverID, rel)
		if err != nil {
			return nil, err
		}
		if entry != nil {
			present = append(present, rel)
		}
	}
	if len(present) == 0 {
		return nil, newError(http.StatusUnprocessableEntity, "nothing to back up: none of the game's backup paths exist yet")
	}
	return present, nil
}

// Backup streams a .tar.gz of the volume (or of `present`, from BackupPaths) to w.
// Top-level names in exclude are left out.
func (f *Files) Backup(ctx context.Context, serverID string, present, exclude []string, w io.Writer) error {
	cmd := []string{"tar", "-czf", "-", "-C", Root}
	for _, pattern := range exclude {
		// Anchored at the top: "./cache" leaves out /data/cache but not config/cache.
		cmd = append(cmd, "--exclude", "./"+pattern)
	}
	if present == nil {
		cmd = append(cmd, ".")
	} else {
		for _, p := range present {
			cmd = append(cmd, "./"+p)
		}
	}
	return f.stream(ctx, serverID, w, cmd...)
}

// Restore replaces the volume (or just `paths`) with a backup, leaving excluded top-level
// entries alone so the server still starts without downloading them again.
func (f *Files) Restore(ctx context.Context, serverID string, paths, exclude []string, archive io.Reader) error {
	if paths == nil {
		cmd := []string{"find", Root, "-mindepth", "1", "-maxdepth", "1"}
		for _, pattern := range exclude {
			cmd = append(cmd, "!", "-name", pattern)
		}
		if _, err := f.run(ctx, serverID, append(cmd, "-exec", "rm", "-rf", "{}", "+")...); err != nil {
			return err
		}
	} else {
		targets := []string{}
		for _, p := range paths {
			if Resolve(p) != "" {
				targets = append(targets, abs(p))
			}
		}
		if len(targets) > 0 {
			if _, err := f.run(ctx, serverID, append([]string{"rm", "-rf"}, targets...)...); err != nil {
				return err
			}
		}
	}
	name, err := f.helper(ctx, serverID)
	if err != nil {
		return err
	}
	// Docker unpacks .tar.gz itself and keeps the owners stored in the archive.
	return f.docker.ContainerExtract(ctx, name, Root, archive)
}
