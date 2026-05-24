package handlers

import (
	"context"
	"net/http"
	"time"
)

// Healthz is the liveness probe.
func (h *Handlers) Healthz(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")

	// Create a context that we use as we're testing health
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()

	var (
		body     []byte
		degraded bool
	)

	// TODO: Check the health of the service

	degraded = false
	body = append(body, []byte("")...)

	if degraded {
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = w.Write([]byte("degraded\n"))
	} else {
		_, _ = w.Write([]byte("ok\n"))
	}
	_, _ = w.Write(body)

	// TODO: remove
	ctx.Done()
}
