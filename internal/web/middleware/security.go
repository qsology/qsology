package middleware

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"net/http"
	"strings"
)

// Stop laughing in the back of the room fellow brits [WM]

// nonceCtxKey is the context key which each per-request CSP nonce
// is stored. Templ will use Nonce() to retrieve it
type nonceCtxKey struct{}

// Nonce returns the CSP nonce attached to a ctx, or  "" if no nonce
// was set
func Nonce(ctx context.Context) string {
	v, _ := ctx.Value(nonceCtxKey{}).(string)
	return v
}

// generateNonce makes a 128bit for CSPRNG in base64 for use in a CSP
// and HTML values. 128bit (16byte) is the recommended size as larger has
// diminishing returns
func generateNonce() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("nonce: %w", err)
	}
	return base64.StdEncoding.EncodeToString(b), nil
}

// buildCSP creates the CSP values for a request. nonce-N is used
// rather than a hash, so that htmx swaped fragements still validate
// if they contain any nonce-tagged scripts.
func buildCSP(nonce string) string {
	var b strings.Builder
	b.Grow(256)
	directives := []string{
		"default-src 'self'",
		"script-src 'self' 'nonce-" + nonce + "'",
		"style-src 'self' 'nonce-" + nonce + "'",
		"img-src 'self' data:",
		"connect-src 'self'",
		"font-src 'self'",
		"form-action 'self'",
		"frame-ancestors 'none'",
		"base-uri 'self'",
		"object-src 'none'",
	}
	for i, d := range directives {
		if i > 0 {
			b.WriteString("; ")
		}
		b.WriteString(d)
	}
	return b.String()
}

// SecurityHeaders adds security headers to responses and a 16byte CSPRNG nonce per request
// CSP does not allow unsafe-inline / unsafe-eval as we do not allowEval or allowScripttags
// and use server rendered fragments, rather than client side needing JS evaluation
// hstsEnabled toggles the STS header. It is True if we're using TLS, an upstream proxy,
// and False if we're plain-http
func SecurityHeaders(hstsEnabled bool) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			nonce, err := generateNonce()
			if err != nil {
				http.Error(w, "internal server error", http.StatusInternalServerError)
				return
			}
			h := w.Header()
			h.Set("Content-Security-Policy", buildCSP(nonce))
			h.Set("X-Frame-Options", "DENY")
			h.Set("X-Content-Type-Options", "nosniff")
			h.Set("Referrer-Policy", "strict-origin-when-cross-origin")
			h.Set("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
			if hstsEnabled {
				h.Set("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
			}
			ctx := context.WithValue(r.Context(), nonceCtxKey{}, nonce)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}
