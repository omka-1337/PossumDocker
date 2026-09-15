package engine

import (
	"encoding/binary"
	"fmt"
	"io"
)

// Without a TTY, Docker sends stdout and stderr over one stream, each chunk behind an 8-byte header:
//
//	[stream: 1=stdout 2=stderr] [0 0 0] [payload size: big-endian uint32] [payload...]
const (
	Stdout = 1
	Stderr = 2
)

// Demux copies each chunk of a multiplexed stream to stdout or stderr until the stream ends.
// A nil writer drops that stream.
func Demux(r io.Reader, stdout, stderr io.Writer) error {
	var header [8]byte
	for {
		if _, err := io.ReadFull(r, header[:]); err != nil {
			if err == io.EOF {
				return nil
			}
			return err
		}
		size := int64(binary.BigEndian.Uint32(header[4:]))
		var dst io.Writer
		switch header[0] {
		case 0, Stdout: // 0 is stdin echoed back, rare; treat it like stdout
			dst = stdout
		case Stderr:
			dst = stderr
		default:
			return fmt.Errorf("unexpected stream id %d in docker output", header[0])
		}
		if dst == nil {
			dst = io.Discard
		}
		if _, err := io.CopyN(dst, r, size); err != nil {
			return err
		}
	}
}
