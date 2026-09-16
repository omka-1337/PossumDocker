package files

import (
	"archive/tar"
	"bytes"
	"errors"
	"io"
	"testing"
)

func TestResolveNeverLeavesTheRoot(t *testing.T) {
	cases := map[string]string{
		"": "", "/": "", "world": "world", "world/": "world", "/a/./b": "a/b",
		"../../etc/passwd": "etc/passwd", "a/../../b": "b",
	}
	for in, want := range cases {
		if got := Resolve(in); got != want {
			t.Errorf("Resolve(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestParseStatKeepsPipesInNames(t *testing.T) {
	entry, ok := parseStat("regular file|12|1789429763|/data/logs/a|b.log")
	if !ok || entry.Name != "a|b.log" || entry.Type != "file" || entry.Size != 12 {
		t.Fatalf("got %+v %v", entry, ok)
	}
	if _, ok := parseStat("garbage"); ok {
		t.Fatal("garbage parsed")
	}
}

func uploadTar(t *testing.T, headers ...*tar.Header) *bytes.Buffer {
	t.Helper()
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	for _, h := range headers {
		if h.Typeflag == 0 {
			h.Typeflag = tar.TypeReg
		}
		if err := tw.WriteHeader(h); err != nil {
			t.Fatal(err)
		}
		if h.Size > 0 {
			tw.Write(bytes.Repeat([]byte("x"), int(h.Size)))
		}
	}
	tw.Close()
	return &buf
}

func TestUploadOwnerIsRewritten(t *testing.T) {
	in := uploadTar(t,
		&tar.Header{Name: "world/", Typeflag: tar.TypeDir, Uid: 0},
		&tar.Header{Name: "world/level.dat", Size: 3, Mode: 0o777, Uid: 0, Uname: "root"},
	)
	var out bytes.Buffer
	if err := rewriteUpload(in, &out, 1000, 1000); err != nil {
		t.Fatal(err)
	}
	tr := tar.NewReader(&out)
	for {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		if h.Uid != 1000 || h.Gid != 1000 || h.Uname != "" {
			t.Errorf("%s: owner %d:%d %q", h.Name, h.Uid, h.Gid, h.Uname)
		}
		if h.Typeflag == tar.TypeReg && h.Mode != 0o644 {
			t.Errorf("%s: mode %o", h.Name, h.Mode)
		}
	}
}

func TestUploadRejectsEscapesAndLinks(t *testing.T) {
	bad := []*tar.Header{
		{Name: "../outside", Size: 1},
		{Name: "a/../../b", Size: 1},
		{Name: "link", Typeflag: tar.TypeSymlink, Linkname: "/etc"},
	}
	for _, h := range bad {
		err := rewriteUpload(uploadTar(t, h), io.Discard, 1000, 1000)
		var fe *Error
		if !errors.As(err, &fe) {
			t.Errorf("%s: expected a user error, got %v", h.Name, err)
		}
	}
}

func TestLastNumber(t *testing.T) {
	du := "300\t/data/a\n4\t/data\n304\ttotal\n"
	if n, err := lastNumber([]byte(du), 1024); err != nil || n != 304*1024 {
		t.Errorf("du: %d, %v", n, err)
	}
	unzip := "Archive:  x.zip\n  Length  Name\n  5000000  a/big.bin\n --------  -------\n  5000002  2 files\n"
	if n, err := lastNumber([]byte(unzip), 1); err != nil || n != 5000002 {
		t.Errorf("unzip: %d, %v", n, err)
	}
	if _, err := lastNumber([]byte("du: /nope: No such file"), 1); err == nil {
		t.Error("garbage accepted")
	}
}

func TestGlobEscape(t *testing.T) {
	if got := globEscape(`a*b?[c]\d`); got != `a\*b\?\[c\]\\d` {
		t.Errorf("globEscape = %q", got)
	}
}
