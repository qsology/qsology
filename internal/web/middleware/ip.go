package middleware

import (
	"context"
	"net"
	"net/http"
	"net/netip"
	"strings"
)

// clientIPCtxKey is the context key carrying the IP address
// Consumers read from this, not the X-Forwarded-For headers
type clientIPCtxKey struct{}

// schemeCtxKey is the context key carrying the scheme
// Consumers read from this, not the X-Forwarded-Proto
type schemeCtxKey struct{}

// ClientIP returns the resolved client IP, or a Zero Addr if none
// was set
func ClientIP(ctx context.Context) netip.Addr {
	v, _ := ctx.Value(clientIPCtxKey{}).(netip.Addr)
	return v
}

// RequestScheme returns the request scheme that the end user
// has accessed this over - allowing for TLS termination upstream
func RequestScheme(ctx context.Context) string {
	v, _ := ctx.Value(schemeCtxKey{}).(string)
	return v
}

// TrustedProxy resolves the IP using the right-to-left X-Forwarded-For
// parsing, bounded by trustedProxies
//
// if trustedProxies is empty, X-Forwarded-* headers are ignored and
// the r.RemoteAddr is used
// Otherwise go over X-Forwarded-For from right to left, poping entries whilst
// they are still trusted. If the entire list is trusted, use r.RemoteAddr
func TrustedProxy(trustedProxies []string) func(http.Handler) http.Handler {
	prefixes, addrs := parseTrustedProxies(trustedProxies)
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			ip := resolveClientIP(r, prefixes, addrs)
			scheme := resolveRequestScheme(r, prefixes, addrs)
			ctx := context.WithValue(r.Context(), clientIPCtxKey{}, ip)
			ctx = context.WithValue(ctx, schemeCtxKey{}, scheme)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

// resolveRequestScheme sets http or https for the end user connection
// if r.TLS != nil, HTTPS (we're terminating TLS)
// if request IP is a trusted proxy, and x-forwarded-proto is set, use this
// fall back to http
func resolveRequestScheme(r *http.Request, prefixes []netip.Prefix, addrs []netip.Addr) string {
	if r.TLS != nil {
		return "https"
	}
	if len(prefixes) == 0 && len(addrs) == 0 {
		// No trusted proxies — refuse to honor X-Forwarded-Proto.
		return "http"
	}
	peer := parseRemoteAddr(r.RemoteAddr)
	if !peer.IsValid() || !isTrusted(peer, prefixes, addrs) {
		return "http"
	}
	xfp := strings.ToLower(strings.TrimSpace(r.Header.Get("X-Forwarded-Proto")))
	if xfp == "https" {
		return "https"
	}
	return "http"
}

// parseRemoteAddr Splits the host from the remote string, returning the parsed address
func parseRemoteAddr(remote string) netip.Addr {
	host, _, err := net.SplitHostPort(remote)
	if err != nil {
		host = remote
	}
	return parseAddr(host)
}

// parseAddr parses the IP Address from a string and returns a Addr
func parseAddr(s string) netip.Addr {
	if a, err := netip.ParseAddr(s); err == nil {
		return a
	}
	return netip.Addr{}
}

// resolveClientIP returns the remote IP address based on the http request
func resolveClientIP(r *http.Request, prefixes []netip.Prefix, addrs []netip.Addr) netip.Addr {
	remote := parseRemoteAddr(r.RemoteAddr)
	if len(prefixes) == 0 && len(addrs) == 0 {
		// No trusted proxies configured — refuse to honor X-Forwarded-*
		// (it would be spoofable). r.RemoteAddr is the authoritative
		// source.
		return remote
	}
	// Walk X-Forwarded-For right-to-left.
	// pop trusted entries, on first untrusted set the ip, or set when none left
	chain := strings.Split(r.Header.Get("X-Forwarded-For"), ",")
	for i := len(chain) - 1; i >= 0; i-- {
		hop := parseAddr(strings.TrimSpace(chain[i]))
		if !hop.IsValid() {
			continue
		}
		if !isTrusted(hop, prefixes, addrs) {
			return hop
		}
	}
	return remote
}

// isTrusted compares the Address or prefix for trusted proxies
func isTrusted(ip netip.Addr, prefixes []netip.Prefix, addrs []netip.Addr) bool {
	for _, a := range addrs {
		if a.Compare(ip) == 0 {
			return true
		}
	}
	for _, p := range prefixes {
		if p.Contains(ip) {
			return true
		}
	}
	return false
}

// parseTrustedProxies creates netip Prefix and Addr instances based off the entries string(s) passed in
func parseTrustedProxies(entries []string) ([]netip.Prefix, []netip.Addr) {
	var prefixes []netip.Prefix
	var addrs []netip.Addr
	for _, s := range entries {
		if p, err := netip.ParsePrefix(s); err == nil {
			prefixes = append(prefixes, p)
			continue
		}
		if a, err := netip.ParseAddr(s); err == nil {
			addrs = append(addrs, a)
		}
	}
	return prefixes, addrs
}
