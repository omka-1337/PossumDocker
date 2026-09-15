package engine

import (
	"bytes"
	"encoding/binary"
	"testing"
)

func frame(stream byte, payload string) []byte {
	header := make([]byte, 8)
	header[0] = stream
	binary.BigEndian.PutUint32(header[4:], uint32(len(payload)))
	return append(header, payload...)
}

func TestDemuxSplitsStdoutAndStderr(t *testing.T) {
	var input bytes.Buffer
	input.Write(frame(Stdout, "hello "))
	input.Write(frame(Stderr, "oops"))
	input.Write(frame(Stdout, "world"))

	var stdout, stderr bytes.Buffer
	if err := Demux(&input, &stdout, &stderr); err != nil {
		t.Fatal(err)
	}
	if stdout.String() != "hello world" || stderr.String() != "oops" {
		t.Fatalf("stdout=%q stderr=%q", stdout.String(), stderr.String())
	}
}

func TestDemuxRejectsGarbage(t *testing.T) {
	if err := Demux(bytes.NewReader(frame(7, "x")), nil, nil); err == nil {
		t.Fatal("expected an error for an unknown stream id")
	}
}

func TestDemuxTruncatedFrame(t *testing.T) {
	data := frame(Stdout, "hello")[:10]
	if err := Demux(bytes.NewReader(data), &bytes.Buffer{}, nil); err == nil {
		t.Fatal("expected an error for a cut-off frame")
	}
}

func TestSplitImage(t *testing.T) {
	cases := map[string][2]string{
		"busybox":                        {"busybox", "latest"},
		"itzg/minecraft-server:java21":   {"itzg/minecraft-server", "java21"},
		"registry:5000/team/game":        {"registry:5000/team/game", "latest"},
		"registry:5000/team/game:stable": {"registry:5000/team/game", "stable"},
	}
	for image, want := range cases {
		name, tag := splitImage(image)
		if name != want[0] || tag != want[1] {
			t.Errorf("splitImage(%q) = %q, %q; want %q, %q", image, name, tag, want[0], want[1])
		}
	}
}
