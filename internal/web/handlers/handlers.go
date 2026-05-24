// Package handlers contains the per-route HTTP handlers.

package handlers

import (
	"log/slog"

	"github.com/qsology/qsology/internal/config"
)

// Handlers carries the dependencies every handler shares.
//
// We add new fields here when the route layer needs a new shared state
// (e.g. a *pgxpool.Pool for the database, a *redis.Client for
// sessions). Each handler method then reads it as h.DB, h.Sessions,
// etc.
type Handlers struct {
	Logger *slog.Logger
	Cfg    config.Config
}

// New constructs a Handlers with the given dependencies.
func New(logger *slog.Logger, cfg config.Config) *Handlers {
	return &Handlers{Logger: logger, Cfg: cfg}
}
