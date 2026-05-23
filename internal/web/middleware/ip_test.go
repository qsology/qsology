package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestTrustedProxy_EmptyTrustListIgnoresXFF(t *testing.T) {
	mw := TrustedProxy(nil)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ip := ClientIP(r.Context())
		if !ip.IsValid() {
			t.Fatal("client IP should be valid")
		}
		if ip.String() != "203.0.113.7" {
			t.Errorf("with no trusted_proxies, client IP must come from RemoteAddr, got %s", ip)
		}
	}))
	r := httptest.NewRequest(http.MethodGet, "/", nil)
	r.RemoteAddr = "203.0.113.7:55555"
	r.Header.Set("X-Forwarded-For", "1.1.1.1, 2.2.2.2")
	mw.ServeHTTP(httptest.NewRecorder(), r)
}

func TestTrustedProxy_PopsTrustedHopsRightToLeft(t *testing.T) {
	// RemoteAddr is the immediate proxy (10.0.0.5). The XFF chain is
	// "1.1.1.1, 10.0.0.4, 10.0.0.5" — the last two are trusted proxies,
	// so the resolved client is 1.1.1.1.
	mw := TrustedProxy([]string{"10.0.0.0/8"})(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ip := ClientIP(r.Context())
		if ip.String() != "1.1.1.1" {
			t.Errorf("expected client 1.1.1.1, got %s", ip)
		}
	}))
	r := httptest.NewRequest(http.MethodGet, "/", nil)
	r.RemoteAddr = "10.0.0.5:5555"
	r.Header.Set("X-Forwarded-For", "1.1.1.1, 10.0.0.4, 10.0.0.5")
	mw.ServeHTTP(httptest.NewRecorder(), r)
}

func TestTrustedProxy_RejectsSpoofedLeftmostHop(t *testing.T) {
	// Attacker sets XFF to "evil-claimed-client, real-attacker".
	// RemoteAddr is the trusted proxy. We must NOT use "evil-claimed-client"
	// just because it's leftmost — we must pop right-to-left.
	mw := TrustedProxy([]string{"10.0.0.0/8"})(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ip := ClientIP(r.Context())
		if ip.String() != "8.8.8.8" {
			t.Errorf("expected 8.8.8.8 (the first non-trusted from the right), got %s", ip)
		}
	}))
	r := httptest.NewRequest(http.MethodGet, "/", nil)
	r.RemoteAddr = "10.0.0.5:5555"
	r.Header.Set("X-Forwarded-For", "evil-spoof, 8.8.8.8")
	mw.ServeHTTP(httptest.NewRecorder(), r)
}

func TestTrustedProxy_FallsBackToRemoteAddrWhenChainAllTrusted(t *testing.T) {
	mw := TrustedProxy([]string{"10.0.0.0/8"})(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ip := ClientIP(r.Context())
		if ip.String() != "10.0.0.5" {
			t.Errorf("when entire chain is trusted, fall back to RemoteAddr; got %s", ip)
		}
	}))
	r := httptest.NewRequest(http.MethodGet, "/", nil)
	r.RemoteAddr = "10.0.0.5:5555"
	r.Header.Set("X-Forwarded-For", "10.0.0.4, 10.0.0.5")
	mw.ServeHTTP(httptest.NewRecorder(), r)
}

func TestClientIP_EmptyContextReturnsInvalid(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/", nil)
	if ClientIP(r.Context()).IsValid() {
		t.Error("client IP without middleware should be invalid (zero Addr)")
	}
}
