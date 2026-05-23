package web

import (
	"log/slog"
	"net/http"

	mw "github.com/qsology/qsology/internal/web/middleware"

	"github.com/qsology/qsology/internal/web/assets"
	"github.com/qsology/qsology/internal/web/views"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/qsology/qsology/internal/config"
	"github.com/qsology/qsology/internal/logging"
)

// Dependencies are service the web server needs but doesn't
// control. Logging, Session service etc
type Dependencies struct {
	Logger *slog.Logger
}

// Server is the HTTP Entry point, using a Chi router
// created at start.
type Server struct {
	cfg     config.Config
	deps    Dependencies
	handler http.Handler
}

// NewServer creates the HTTP Server and links the router
func NewServer(cfg config.Config, deps Dependencies) *Server {
	s := &Server{cfg: cfg, deps: deps}
	s.handler = s.BuildRouter()
	return s
}

// Handler returns the HTTP Handler
func (s *Server) Handler() http.Handler { return s.handler }

// ServeHTTP handles the handler so Server can be called directly
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.handler.ServeHTTP(w, r)
}

// BuildRouter builds out the router with the middleware
func (s *Server) BuildRouter() http.Handler {
	// Create router
	r := chi.NewRouter()

	// Middleware
	//// Trusted Proxies
	r.Use(mw.TrustedProxy(s.cfg.HTTP.TrustedProxies))
	//// RequestID injection
	r.Use(middleware.RequestID)
	//// Per-request logging (configurable; toggle + per-status levels +
	//// debug-path demotion all come from config.Logging.Requests).
	if s.cfg.Logging.Requests.Enabled {
		r.Use(mw.RequestLogger(s.deps.Logger, mw.RequestLoggerOptions{
			LevelSuccess:     mustLevel(s.cfg.Logging.Requests.LevelSuccess),
			LevelClientError: mustLevel(s.cfg.Logging.Requests.LevelClientError),
			LevelServerError: mustLevel(s.cfg.Logging.Requests.LevelServerError),
			DebugPathGlobs:   s.cfg.Logging.Requests.DebugPaths,
		}))
	}

	//// Security Headers
	////// TODO: Update this when TLS is enabled
	hstsEnabled := false
	r.Use(mw.SecurityHeaders(hstsEnabled))

	// Embedded static assets
	r.Handle("/static/*", assets.Handler())

	// 404
	r.NotFound(s.handleNotFound)

	return r
}

// mustLevel parses a slog level string. Config validation has already
// run, so a parse failure here means a bug in the validator — panic so
// the failure is loud.
func mustLevel(s string) slog.Level {
	lvl, err := logging.ParseLevel(s)
	if err != nil {
		panic("web: unparseable log level after config validation: " + err.Error())
	}
	return lvl
}

// Return a 404 using a tmpl 404 page, or plain text if the render fails
func (s *Server) handleNotFound(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusNotFound)
	if err := views.NotFound().Render(r.Context(), w); err != nil {
		http.Error(w, "not found", http.StatusNotFound)
	}
}
