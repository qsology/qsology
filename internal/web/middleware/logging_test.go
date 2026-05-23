package middleware

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"testing"

	chimw "github.com/go-chi/chi/v5/middleware"
)

// newTestLogger returns a JSON slog logger writing into buf at the given level.
func newTestLogger(buf *bytes.Buffer, level slog.Level) *slog.Logger {
	return slog.New(slog.NewJSONHandler(buf, &slog.HandlerOptions{Level: level}))
}

// defaultOpts mirrors the package defaults (info/warn/error) so most tests can call runRequest
// without spelling them out.
func defaultOpts(debugGlobs []string) RequestLoggerOptions {
	return RequestLoggerOptions{
		LevelSuccess:     slog.LevelInfo,
		LevelClientError: slog.LevelWarn,
		LevelServerError: slog.LevelError,
		DebugPathGlobs:   debugGlobs,
	}
}

// runRequest installs RequestLogger around handler, primes the context with the
// values the middleware expects (request id, client ip, scheme) and serves request.
func runRequest(t *testing.T, logger *slog.Logger, opts RequestLoggerOptions, handler http.HandlerFunc, req *http.Request) *httptest.ResponseRecorder {
	t.Helper()
	ip := netip.MustParseAddr("203.0.113.5")
	ctx := context.WithValue(req.Context(), clientIPCtxKey{}, ip)
	ctx = context.WithValue(ctx, schemeCtxKey{}, "http")
	ctx = context.WithValue(ctx, chimw.RequestIDKey, "req-test-1")
	req = req.WithContext(ctx)
	rec := httptest.NewRecorder()
	RequestLogger(logger, opts)(handler).ServeHTTP(rec, req)
	return rec
}

// decodeOne decodes the single JSON log line in buf. Fails if 0 or >1 lines.
func decodeOne(t *testing.T, buf *bytes.Buffer) map[string]any {
	t.Helper()
	lines := strings.Split(strings.TrimRight(buf.String(), "\n"), "\n")
	if len(lines) != 1 || lines[0] == "" {
		t.Fatalf("expected 1 log line, got %d: %q", len(lines), buf.String())
	}
	var m map[string]any
	if err := json.Unmarshal([]byte(lines[0]), &m); err != nil {
		t.Fatalf("decode: %v (line=%s)", err, lines[0])
	}
	return m
}

func TestRequestLogger_LogsExpectedFieldsAtInfo(t *testing.T) {
	var buf bytes.Buffer
	logger := newTestLogger(&buf, slog.LevelDebug)

	req := httptest.NewRequest(http.MethodGet, "/qsos", nil)
	rec := runRequest(t, logger, defaultOpts([]string{"/static/**"}), func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("hello"))
	}, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("handler status = %d, want 200", rec.Code)
	}

	m := decodeOne(t, &buf)
	if m["level"] != "INFO" {
		t.Errorf("level = %v, want INFO", m["level"])
	}
	if m["msg"] != "http_request" {
		t.Errorf("msg = %v, want http_request", m["msg"])
	}
	if m["client_ip"] != "203.0.113.5" {
		t.Errorf("client_ip = %v", m["client_ip"])
	}
	if m["request_id"] != "req-test-1" {
		t.Errorf("request_id = %v", m["request_id"])
	}
	http_, _ := m["http"].(map[string]any)
	if http_ == nil {
		t.Fatalf("http group missing: %v", m)
	}
	if http_["method"] != "GET" {
		t.Errorf("http.method = %v", http_["method"])
	}
	if http_["path"] != "/qsos" {
		t.Errorf("http.path = %v", http_["path"])
	}
	if int(http_["status"].(float64)) != 200 {
		t.Errorf("http.status = %v", http_["status"])
	}
	if int(http_["bytes"].(float64)) != 5 {
		t.Errorf("http.bytes = %v, want 5", http_["bytes"])
	}
	if http_["scheme"] != "http" {
		t.Errorf("http.scheme = %v", http_["scheme"])
	}
	if _, ok := http_["duration_ms"].(float64); !ok {
		t.Errorf("http.duration_ms missing or not numeric: %v", http_["duration_ms"])
	}
}

func TestRequestLogger_DemotesDebugPathGlob(t *testing.T) {
	// With handler level INFO, a /static/ success must be filtered out.
	var buf bytes.Buffer
	logger := newTestLogger(&buf, slog.LevelInfo)

	req := httptest.NewRequest(http.MethodGet, "/static/app.css", nil)
	runRequest(t, logger, defaultOpts([]string{"/static/**"}), func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}, req)

	if buf.Len() != 0 {
		t.Errorf("expected no log output for /static/ at INFO, got: %s", buf.String())
	}

	// At DEBUG level it should appear, tagged DEBUG.
	buf.Reset()
	logger = newTestLogger(&buf, slog.LevelDebug)
	req = httptest.NewRequest(http.MethodGet, "/static/app.css", nil)
	runRequest(t, logger, defaultOpts([]string{"/static/**"}), func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}, req)

	m := decodeOne(t, &buf)
	if m["level"] != "DEBUG" {
		t.Errorf("level = %v, want DEBUG", m["level"])
	}
}

func TestRequestLogger_ErrorsOnDebugPathStillSurface(t *testing.T) {
	// 404 on /static/foo must log at WARN even though path is debug-prefixed.
	var buf bytes.Buffer
	logger := newTestLogger(&buf, slog.LevelInfo)

	req := httptest.NewRequest(http.MethodGet, "/static/missing.css", nil)
	runRequest(t, logger, defaultOpts([]string{"/static/**"}), func(w http.ResponseWriter, _ *http.Request) {
		http.NotFound(w, req)
	}, req)

	m := decodeOne(t, &buf)
	if m["level"] != "WARN" {
		t.Errorf("level = %v, want WARN", m["level"])
	}
}

func TestRequestLogger_StatusMapping(t *testing.T) {
	cases := []struct {
		status int
		want   string
	}{
		{200, "INFO"},
		{302, "INFO"},
		{400, "WARN"},
		{404, "WARN"},
		{500, "ERROR"},
		{503, "ERROR"},
	}
	for _, c := range cases {
		var buf bytes.Buffer
		logger := newTestLogger(&buf, slog.LevelDebug)
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		runRequest(t, logger, defaultOpts(nil), func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(c.status)
		}, req)
		m := decodeOne(t, &buf)
		if m["level"] != c.want {
			t.Errorf("status %d: level = %v, want %s", c.status, m["level"], c.want)
		}
	}
}

func TestRequestLogger_StatusDefaultsTo200WhenHandlerSilent(t *testing.T) {
	var buf bytes.Buffer
	logger := newTestLogger(&buf, slog.LevelDebug)
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	runRequest(t, logger, defaultOpts(nil), func(_ http.ResponseWriter, _ *http.Request) {
		// no WriteHeader, no Write — net/http implicitly sends 200
	}, req)
	m := decodeOne(t, &buf)
	http_, _ := m["http"].(map[string]any)
	if int(http_["status"].(float64)) != 200 {
		t.Errorf("status = %v, want 200", http_["status"])
	}
	if int(http_["bytes"].(float64)) != 0 {
		t.Errorf("bytes = %v, want 0", http_["bytes"])
	}
}

func TestRequestLogger_GlobSemantics(t *testing.T) {
	// Verify the glob distinctions: `*` is single-segment, `**` crosses
	// segments, and a literal is exact-match.
	cases := []struct {
		name        string
		globs       []string
		path        string
		wantDemoted bool
	}{
		{"** matches nested", []string{"/static/**"}, "/static/vendor/pico.css", true},
		{"** matches single level", []string{"/static/**"}, "/static/app.css", true},
		{"** matches base", []string{"/static/**"}, "/static/", true},
		{"single * does not cross /", []string{"/static/*"}, "/static/vendor/pico.css", false},
		{"single * matches one segment", []string{"/static/*"}, "/static/app.css", true},
		{"literal exact match", []string{"/healthz"}, "/healthz", true},
		{"literal does not match prefix", []string{"/health"}, "/healthz", false},
		{"non-matching path", []string{"/static/**"}, "/qsos", false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			var buf bytes.Buffer
			logger := newTestLogger(&buf, slog.LevelDebug)
			req := httptest.NewRequest(http.MethodGet, c.path, nil)
			runRequest(t, logger, defaultOpts(c.globs), func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(http.StatusOK)
			}, req)
			m := decodeOne(t, &buf)
			gotDemoted := m["level"] == "DEBUG"
			if gotDemoted != c.wantDemoted {
				t.Errorf("path=%q globs=%v: level=%v wantDemoted=%v", c.path, c.globs, m["level"], c.wantDemoted)
			}
		})
	}
}

func TestRequestLogger_HonorsCustomLevelMapping(t *testing.T) {
	// Set all three buckets to DEBUG (e.g. for noisy debug session).
	opts := RequestLoggerOptions{
		LevelSuccess:     slog.LevelDebug,
		LevelClientError: slog.LevelDebug,
		LevelServerError: slog.LevelDebug,
	}
	for _, status := range []int{200, 404, 500} {
		var buf bytes.Buffer
		logger := newTestLogger(&buf, slog.LevelDebug)
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		runRequest(t, logger, opts, func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(status)
		}, req)
		m := decodeOne(t, &buf)
		if m["level"] != "DEBUG" {
			t.Errorf("status %d: level = %v, want DEBUG", status, m["level"])
		}
	}
}

func TestRequestLogger_IncludesUpstreamRequestIDWhenPresent(t *testing.T) {
	var buf bytes.Buffer
	logger := newTestLogger(&buf, slog.LevelDebug)
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	// Prime the upstream value directly (RequestID middleware normally sets it).
	ctx := context.WithValue(req.Context(), upstreamRequestIDCtxKey{}, "trace-upstream-1")
	req = req.WithContext(ctx)
	runRequest(t, logger, defaultOpts(nil), func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}, req)
	m := decodeOne(t, &buf)
	if m["upstream_request_id"] != "trace-upstream-1" {
		t.Errorf("upstream_request_id = %v, want trace-upstream-1", m["upstream_request_id"])
	}
}

func TestRequestLogger_OmitsUpstreamRequestIDWhenAbsent(t *testing.T) {
	var buf bytes.Buffer
	logger := newTestLogger(&buf, slog.LevelDebug)
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	runRequest(t, logger, defaultOpts(nil), func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}, req)
	m := decodeOne(t, &buf)
	if _, ok := m["upstream_request_id"]; ok {
		t.Errorf("upstream_request_id should be omitted when not set, got %v", m["upstream_request_id"])
	}
}

func TestRequestLogger_AccumulatesBytesAcrossWrites(t *testing.T) {
	var buf bytes.Buffer
	logger := newTestLogger(&buf, slog.LevelDebug)
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	runRequest(t, logger, defaultOpts(nil), func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("hello "))
		_, _ = w.Write([]byte("world"))
	}, req)
	m := decodeOne(t, &buf)
	http_, _ := m["http"].(map[string]any)
	if int(http_["bytes"].(float64)) != 11 {
		t.Errorf("bytes = %v, want 11", http_["bytes"])
	}
}
