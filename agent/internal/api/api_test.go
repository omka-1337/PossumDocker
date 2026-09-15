package api

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
)

func testHandler() http.Handler {
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	return New("0123456789abcdef0123456789abcdef", nil, nil, log).Handler()
}

func TestTokenIsRequired(t *testing.T) {
	handler := testHandler()
	for _, auth := range []string{"", "Bearer wrong", "0123456789abcdef0123456789abcdef"} {
		req := httptest.NewRequest(http.MethodGet, "/v1/servers/abc", nil)
		if auth != "" {
			req.Header.Set("Authorization", auth)
		}
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusUnauthorized {
			t.Errorf("Authorization %q: status %d, want 401", auth, rec.Code)
		}
	}
}

func TestHealthNeedsNoToken(t *testing.T) {
	rec := httptest.NewRecorder()
	testHandler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/health", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
}

func TestServerIDsAreValidated(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/v1/servers/bad..id/start", nil)
	req.Header.Set("Authorization", "Bearer 0123456789abcdef0123456789abcdef")
	rec := httptest.NewRecorder()
	testHandler().ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status %d, want 400", rec.Code)
	}
}
