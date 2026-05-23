package assets

import (
	"io/fs"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestEmbeddedFilesPresent(t *testing.T) {
	for _, p := range []string{
		"app.css",
		"app.js",
		"vendor/htmx-2.0.9.min.js",
		"vendor/pico-2.0.6.min.css",
	} {
		if _, err := fs.ReadFile(FS, p); err != nil {
			t.Errorf("embedded asset %s missing: %v", p, err)
		}
	}
}

func TestSRI_StableAcrossCalls(t *testing.T) {
	a, err := SRI("app.css")
	if err != nil {
		t.Fatalf("SRI: %v", err)
	}
	b, err := SRI("app.css")
	if err != nil {
		t.Fatalf("SRI: %v", err)
	}
	if a != b {
		t.Errorf("SRI not stable across calls: %s vs %s", a, b)
	}
	if !strings.HasPrefix(a, "sha256-") {
		t.Errorf("expected sha256- prefix, got %s", a)
	}
}

func TestHandler_ServesAsset(t *testing.T) {
	h := Handler()
	req := httptest.NewRequest(http.MethodGet, "/static/app.css", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rec.Code)
	}
	if !strings.HasPrefix(rec.Header().Get("Content-Type"), "text/css") {
		t.Errorf("content-type = %s", rec.Header().Get("Content-Type"))
	}
	if !strings.Contains(rec.Header().Get("Cache-Control"), "immutable") {
		t.Errorf("cache-control should mark immutable, got %s", rec.Header().Get("Cache-Control"))
	}
}

func TestHandler_RejectsPathTraversal(t *testing.T) {
	h := Handler()
	req := httptest.NewRequest(http.MethodGet, "/static/../etc/passwd", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Errorf("path traversal must 404, got %d", rec.Code)
	}
}

func TestHandler_404OnMissing(t *testing.T) {
	h := Handler()
	req := httptest.NewRequest(http.MethodGet, "/static/nope.css", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Errorf("missing asset should 404, got %d", rec.Code)
	}
}
