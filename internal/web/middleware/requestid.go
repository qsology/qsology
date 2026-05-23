package middleware

import (
	"context"
	"net"
	"net/http"
	"strings"

	chimw "github.com/go-chi/chi/v5/middleware"
	"github.com/google/uuid"
)

// upstreamRequestIDCtxKey holds the inbound X-Request-Id header
// value (if any), regardless of whether we trust it for use as our
// own request id. RequestLogger uses it as upstream_request_id.
type upstreamRequestIDCtxKey struct{}

// UpstreamRequestID returns the value of the inbound X-Request-Id
// header on this request, or "" if none was present.
func UpstreamRequestID(ctx context.Context) string {
	v, _ := ctx.Value(upstreamRequestIDCtxKey{}).(string)
	return v
}

// maxInboundRequestIDLen sets how much of an inbound X-Request-Id we
// keep. The header is otherwise client-controlled and we'd rather not
// store/echo an uncontrolled blob of data.
const maxInboundRequestIDLen = 200

// RequestID assigns a request id of the form "<host>-<uuid>" to each
// request and stores it in chi's RequestID context slot so existing
// consumers (e.g. RequestLogger, chimw.GetReqID) pick it up. It also
// echoes the chosen id back as the X-Request-Id response header.
//
// An inbound X-Request-Id is honored as the request id only when the
// request comes from a configured trusted proxy (PeerTrusted). For
// untrusted peers a fresh id is always generated. Either way, the raw
// inbound value is preserved in context for RequestLogger to log as
// upstream_request_id.
//
// Must be installed after TrustedProxy so PeerTrusted is populated.
func RequestID(fallbackHost string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			inbound := sanitizeInbound(r.Header.Get("X-Request-Id"))

			var id string
			if inbound != "" && PeerTrusted(r.Context()) {
				id = inbound
			} else {
				// UUIDv7 is time-ordered, which makes request ids sort
				// naturally by arrival time in log aggregators.
				// NewV7 only errors if crypto/rand fails — if that's
				// broken we have larger problems than a request id.
				id = hostPart(r.Host, fallbackHost) + "-" + uuid.Must(uuid.NewV7()).String()
			}

			ctx := r.Context()
			if inbound != "" {
				ctx = context.WithValue(ctx, upstreamRequestIDCtxKey{}, inbound)
			}
			ctx = context.WithValue(ctx, chimw.RequestIDKey, id)

			w.Header().Set("X-Request-Id", id)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

// sanitizeInbound truncates oversized values and strips CR/LF to avoid
// header-injection echoes. Other characters are left alone — the JSON
// log handler escapes them safely.
func sanitizeInbound(v string) string {
	if v == "" {
		return ""
	}
	if len(v) > maxInboundRequestIDLen {
		v = v[:maxInboundRequestIDLen]
	}
	v = strings.Map(func(r rune) rune {
		if r == '\r' || r == '\n' {
			return -1
		}
		return r
	}, v)
	return v
}

// hostPart returns the host portion to embed in the request id. It
// prefers the Host header (port stripped, lowercased), falling back to
// fallbackHost when Host is empty or unparseable.
func hostPart(host, fallback string) string {
	host = strings.TrimSpace(host)
	if host == "" {
		return fallback
	}
	if h, _, err := net.SplitHostPort(host); err == nil {
		host = h
	}
	return strings.ToLower(host)
}
