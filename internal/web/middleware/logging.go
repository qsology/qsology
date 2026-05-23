package middleware

import (
	"log/slog"
	"net/http"
	"time"

	"github.com/bmatcuk/doublestar/v4"
	chimw "github.com/go-chi/chi/v5/middleware"
)

// RequestLoggerOptions configures the per-request access log emitted by
// RequestLogger. LevelSuccess applies to 2xx/3xx, LevelClientError to 4xx,
// LevelServerError to 5xx. DebugPathGlobs lists doublestar globs (e.g.
// "/static/**", "/healthz") whose 2xx/3xx responses are demoted to DEBUG;
// 4xx/5xx still use the configured error levels so failures keep
// surfacing. Patterns are matched against the URL path with `/` as the
// separator. Invalid patterns are silently ignored at match time — pass
// only patterns that have been validated up front.
type RequestLoggerOptions struct {
	LevelSuccess     slog.Level
	LevelClientError slog.Level
	LevelServerError slog.Level
	DebugPathGlobs   []string
}

// RequestLogger emits a structured slog record per HTTP request.
// Must be installed after TrustedProxy and chi's RequestID so the
// client_ip, scheme, and request_id values are already on the context.
func RequestLogger(logger *slog.Logger, opts RequestLoggerOptions) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			ww := chimw.NewWrapResponseWriter(w, r.ProtoMajor)
			next.ServeHTTP(ww, r)
			dur := time.Since(start)

			status := ww.Status()
			if status == 0 {
				// Handler returned without Write or WriteHeader; net/http
				// sends an implicit 200 OK in that case.
				status = http.StatusOK
			}

			attrs := []slog.Attr{
				slog.Group("http",
					slog.String("method", r.Method),
					slog.String("path", r.URL.Path),
					slog.Int("status", status),
					slog.Int("bytes", ww.BytesWritten()),
					slog.Float64("duration_ms", float64(dur.Microseconds())/1000.0),
					slog.String("scheme", RequestScheme(r.Context())),
				),
				slog.String("client_ip", ClientIP(r.Context()).String()),
				slog.String("request_id", chimw.GetReqID(r.Context())),
			}
			if upstream := UpstreamRequestID(r.Context()); upstream != "" {
				attrs = append(attrs, slog.String("upstream_request_id", upstream))
			}
			logger.LogAttrs(r.Context(), opts.levelFor(status, r.URL.Path), "http_request", attrs...)
		})
	}
}

func (o RequestLoggerOptions) levelFor(status int, path string) slog.Level {
	switch {
	case status >= 500:
		return o.LevelServerError
	case status >= 400:
		return o.LevelClientError
	}
	for _, pattern := range o.DebugPathGlobs {
		// doublestar.Match returns an error for malformed patterns.
		// Config validation rejects those, so so assume we're no matching on error
		if matched, _ := doublestar.Match(pattern, path); matched {
			return slog.LevelDebug
		}
	}
	return o.LevelSuccess
}
