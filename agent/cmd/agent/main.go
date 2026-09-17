// The agent: the only part of possum that talks to Docker. The panel sends it
// container specs and file operations over HTTP, authenticated with a shared token.
//
//	POSSUM_AGENT_TOKEN   required, the same value the panel has
//	POSSUM_AGENT_LISTEN  address to listen on (default :8081)
//	DOCKER_HOST       Docker daemon (default unix:///var/run/docker.sock)
//	POSSUM_LOG_LEVEL     debug, info (default), warn, error
//
// `agent healthcheck` checks a running agent, for the container's HEALTHCHECK (the image has no shell).
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/omka-1337/possum/agent/internal/api"
	"github.com/omka-1337/possum/agent/internal/engine"
	"github.com/omka-1337/possum/agent/internal/files"
	"github.com/omka-1337/possum/agent/internal/runtime"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(healthcheck())
	}
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "agent:", err)
		os.Exit(1)
	}
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func run() error {
	var level slog.Level
	if err := level.UnmarshalText([]byte(env("POSSUM_LOG_LEVEL", "info"))); err != nil {
		return fmt.Errorf("POSSUM_LOG_LEVEL: %w", err)
	}
	log := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: level}))

	token := os.Getenv("POSSUM_AGENT_TOKEN")
	if len(token) < 32 {
		return errors.New("POSSUM_AGENT_TOKEN must be set to a random value of at least 32 characters")
	}
	docker, err := engine.New(env("DOCKER_HOST", "unix:///var/run/docker.sock"))
	if err != nil {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	fs := files.New(docker, log)
	rt := runtime.New(docker, fs, log)
	go fs.ReapIdle(ctx)

	server := &http.Server{
		Addr:              env("POSSUM_AGENT_LISTEN", ":8081"),
		Handler:           api.New(token, rt, fs, log).Handler(),
		ReadHeaderTimeout: 10 * time.Second,
		// No write timeout: console logs, installs and backups stream for as long as they take.
	}
	errs := make(chan error, 1)
	go func() {
		log.Info("agent listening", "addr", server.Addr)
		errs <- server.ListenAndServe()
	}()

	select {
	case err := <-errs:
		return err
	case <-ctx.Done():
		log.Info("shutting down")
		shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		return server.Shutdown(shutdown)
	}
}

func healthcheck() int {
	addr := env("POSSUM_AGENT_LISTEN", ":8081")
	if addr[0] == ':' {
		addr = "127.0.0.1" + addr
	}
	client := http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://" + addr + "/health")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}
