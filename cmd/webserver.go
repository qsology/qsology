package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/qsology/qsology/internal/web"
	"github.com/urfave/cli/v3"
)

func serveCommand() *cli.Command {
	return &cli.Command{
		Name:  "serve",
		Usage: "run the HTTP server",
		Action: func(ctx context.Context, cmd *cli.Command) error {
			return runServe(ctx, cmd)
		},
	}
}

func runServe(ctx context.Context, cmd *cli.Command) error {
	cfg, logger, err := bootstrap(cmd)
	if err != nil {
		return err
	}

	logger.Info("starting qsology",
		slog.String("version", buildVersion),
		slog.String("address", cfg.HTTP.Address),
		slog.Int("port", cfg.HTTP.Port),
		slog.Bool("behind_proxy", cfg.HTTP.BehindProxy),
	)

	// Create server instance
	srv := web.NewServer(cfg, web.Dependencies{
		Logger: logger,
	})

	// Build server addr
	addr := net.JoinHostPort(cfg.HTTP.Address, strconv.Itoa(cfg.HTTP.Port))

	// Create net/http server instance
	httpServer := &http.Server{
		Addr:    addr,
		Handler: srv,
		// Slow-client / slow-loris DoS protection. Values are generous
		// for legitimate slow networks but bounded.
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       120 * time.Second,
		ErrorLog:          slog.NewLogLogger(logger.Handler(), slog.LevelError),
	}

	// Create context and signals for shutdown
	shutdownCtx, stop := signal.NotifyContext(ctx, os.Interrupt, syscall.SIGTERM)
	defer stop()

	// Create channel and start webserver
	errCh := make(chan error, 1)
	go func() {
		defer close(errCh)
		var srvErr error
		logger.Info("http server listening", slog.String("addr", addr))
		srvErr = httpServer.ListenAndServe()
		if srvErr != nil && !errors.Is(srvErr, http.ErrServerClosed) {
			errCh <- srvErr
		}
	}()

	// Run a select on the error channel, if there's an error return it
	// if there is a shutdown requested, log it
	select {
	case err := <-errCh:
		if err != nil {
			return fmt.Errorf("server: %w", err)
		}
		return nil
	case <-shutdownCtx.Done():
		logger.Info("shutdown signal received, draining connections")
	}

	// Wait the grace period and shutdown
	gracePeriod, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := httpServer.Shutdown(gracePeriod); err != nil {
		return fmt.Errorf("graceful shutdown: %w", err)
	}
	logger.Info("shutdown complete")
	return nil
}
