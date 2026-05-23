package middleware

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSecurityHeaders_SetsHeaders(t *testing.T) {
	h := SecurityHeaders(true)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/", nil))

	for k := range map[string]struct{}{
		"Content-Security-Policy":   {},
		"X-Frame-Options":           {},
		"X-Content-Type-Options":    {},
		"Referrer-Policy":           {},
		"Permissions-Policy":        {},
		"Strict-Transport-Security": {},
	} {
		if rec.Header().Get(k) == "" {
			t.Errorf("%s header missing", k)
		}
	}
}

func TestSecurityHeaders_NonceFlowsToContext(t *testing.T) {
	var capturedNonce string
	h := SecurityHeaders(false)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		capturedNonce = Nonce(r.Context())
	}))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/", nil))

	if capturedNonce == "" {
		t.Fatal("nonce should be set in context")
	}
	csp := rec.Header().Get("Content-Security-Policy")
	if !strings.Contains(csp, "'nonce-"+capturedNonce+"'") {
		t.Errorf("CSP header should reference the same nonce as context.\ncsp=%s\nnonce=%s", csp, capturedNonce)
	}
}

func TestSecurityHeaders_NonceIsPerRequest(t *testing.T) {
	var nonces []string
	h := SecurityHeaders(false)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		nonces = append(nonces, Nonce(r.Context()))
	}))
	for i := 0; i < 3; i++ {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/", nil))
	}
	seen := make(map[string]bool)
	for _, n := range nonces {
		if seen[n] {
			t.Errorf("nonce %q reused across requests", n)
		}
		seen[n] = true
	}
}

func TestSecurityHeaders_NoHSTSWhenDisabled(t *testing.T) {
	h := SecurityHeaders(false)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/", nil))
	if rec.Header().Get("Strict-Transport-Security") != "" {
		t.Error("HSTS must not be set when hstsEnabled=false")
	}
}

func TestNonce_EmptyContextReturnsEmpty(t *testing.T) {
	if Nonce(context.Background()) != "" {
		t.Error("Nonce on plain context should return empty string")
	}
}
