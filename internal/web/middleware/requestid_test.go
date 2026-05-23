package middleware

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	chimw "github.com/go-chi/chi/v5/middleware"
)

// runRequestID installs RequestID around the chain and captures the
// chosen request id, the X-Request-Id response header, and the
// upstream value seen via UpstreamRequestID. peerTrusted toggles the
// trust context value the middleware reads.
type ridResult struct {
	requestID         string
	upstreamRequestID string
	responseHeader    string
}

func runRequestID(t *testing.T, fallback string, peerTrusted bool, inboundXRID string, host string) ridResult {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Host = host
	if inboundXRID != "" {
		req.Header.Set("X-Request-Id", inboundXRID)
	}
	req = req.WithContext(context.WithValue(req.Context(), peerTrustedCtxKey{}, peerTrusted))

	var got ridResult
	handler := RequestID(fallback)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got.requestID, _ = r.Context().Value(chimw.RequestIDKey).(string)
		got.upstreamRequestID = UpstreamRequestID(r.Context())
	}))
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	got.responseHeader = rec.Header().Get("X-Request-Id")
	return got
}

func TestRequestID_GeneratesFreshForUntrustedPeer(t *testing.T) {
	r := runRequestID(t, "qsology.com", false, "client-supplied-id", "example.com")
	if !strings.HasPrefix(r.requestID, "example.com-") {
		t.Errorf("requestID = %q, want prefix example.com-", r.requestID)
	}
	if r.requestID == "client-supplied-id" {
		t.Error("untrusted inbound X-Request-Id must NOT be used as the request id")
	}
	if r.upstreamRequestID != "client-supplied-id" {
		t.Errorf("upstreamRequestID = %q, want client-supplied-id", r.upstreamRequestID)
	}
	if r.responseHeader != r.requestID {
		t.Errorf("X-Request-Id response = %q, want %q", r.responseHeader, r.requestID)
	}
}

func TestRequestID_HonorsInboundFromTrustedPeer(t *testing.T) {
	r := runRequestID(t, "qsology.com", true, "trace-abc-123", "example.com")
	if r.requestID != "trace-abc-123" {
		t.Errorf("requestID = %q, want trace-abc-123", r.requestID)
	}
	if r.upstreamRequestID != "trace-abc-123" {
		t.Errorf("upstreamRequestID = %q, want trace-abc-123", r.upstreamRequestID)
	}
}

func TestRequestID_GeneratesWhenNoInboundEvenIfTrusted(t *testing.T) {
	r := runRequestID(t, "qsology.com", true, "", "example.com")
	if !strings.HasPrefix(r.requestID, "example.com-") {
		t.Errorf("requestID = %q, want prefix example.com-", r.requestID)
	}
	if r.upstreamRequestID != "" {
		t.Errorf("upstreamRequestID = %q, want empty", r.upstreamRequestID)
	}
}

func TestRequestID_UsesFallbackHostWhenHostEmpty(t *testing.T) {
	r := runRequestID(t, "qsology.com", false, "", "")
	if !strings.HasPrefix(r.requestID, "qsology.com-") {
		t.Errorf("requestID = %q, want prefix qsology.com-", r.requestID)
	}
}

func TestRequestID_StripsPortAndLowercasesHost(t *testing.T) {
	r := runRequestID(t, "qsology.com", false, "", "Example.COM:8080")
	if !strings.HasPrefix(r.requestID, "example.com-") {
		t.Errorf("requestID = %q, want lowercased host without port", r.requestID)
	}
}

func TestRequestID_SuffixIsUUIDv7(t *testing.T) {
	r := runRequestID(t, "qsology.com", false, "", "example.com")
	suffix := strings.TrimPrefix(r.requestID, "example.com-")
	// Canonical UUID: 8-4-4-4-12 hex digits, separated by hyphens.
	parts := strings.Split(suffix, "-")
	if len(parts) != 5 {
		t.Fatalf("uuid suffix %q does not have 5 hyphen-separated parts", suffix)
	}
	wantLens := []int{8, 4, 4, 4, 12}
	for i, p := range parts {
		if len(p) != wantLens[i] {
			t.Errorf("uuid part %d %q len=%d want=%d", i, p, len(p), wantLens[i])
		}
	}
	// Version nibble lives at the first character of the third block
	// (e.g. xxxxxxxx-xxxx-7xxx-xxxx-xxxxxxxxxxxx for v7).
	if parts[2][0] != '7' {
		t.Errorf("uuid version nibble = %q, want 7 (got suffix %q)", parts[2][0], suffix)
	}
}

func TestRequestID_FreshIDsAreTimeOrdered(t *testing.T) {
	// UUIDv7's leading 48 bits encode a unix-ms timestamp, so ids
	// generated in sequence should sort lexicographically.
	var ids []string
	for i := 0; i < 5; i++ {
		r := runRequestID(t, "qsology.com", false, "", "example.com")
		ids = append(ids, strings.TrimPrefix(r.requestID, "example.com-"))
	}
	for i := 1; i < len(ids); i++ {
		if ids[i-1] > ids[i] {
			t.Errorf("uuids not in ascending order: %q > %q", ids[i-1], ids[i])
		}
	}
}

func TestRequestID_TruncatesOversizedInbound(t *testing.T) {
	long := strings.Repeat("x", maxInboundRequestIDLen+50)
	r := runRequestID(t, "qsology.com", true, long, "example.com")
	if len(r.requestID) != maxInboundRequestIDLen {
		t.Errorf("requestID len = %d, want %d (truncated)", len(r.requestID), maxInboundRequestIDLen)
	}
}

func TestRequestID_StripsCRLFFromInbound(t *testing.T) {
	r := runRequestID(t, "qsology.com", true, "abc\r\ndef", "example.com")
	if r.requestID != "abcdef" {
		t.Errorf("requestID = %q, want CR/LF stripped to abcdef", r.requestID)
	}
}

func TestRequestID_FreshIDsAreUnique(t *testing.T) {
	seen := make(map[string]struct{})
	for i := 0; i < 100; i++ {
		r := runRequestID(t, "qsology.com", false, "", "example.com")
		if _, dup := seen[r.requestID]; dup {
			t.Fatalf("duplicate request id generated: %s", r.requestID)
		}
		seen[r.requestID] = struct{}{}
	}
}
