package logging

import (
	"fmt"
	"io"
	"log/slog"
	"os"
	"strings"

	"github.com/qsology/qsology/internal/config"
)

// New constructs a slog.Logger to write to os.Stderr
func New(cfg config.LoggingConfig) (*slog.Logger, error) { return NewWith(cfg, os.Stderr) }

// NewWith is used by New and when testing
func NewWith(cfg config.LoggingConfig, out io.Writer) (*slog.Logger, error) {
	level, err := parseLevel(cfg.Level)
	if err != nil {
		return nil, err
	}

	opts := &slog.HandlerOptions{
		Level:       level,
		AddSource:   level == slog.LevelDebug,
		ReplaceAttr: redactSecrets,
	}

	var handler slog.Handler

	switch strings.ToLower(cfg.Format) {
	case "json":
		handler = slog.NewJSONHandler(out, opts)
	case "text":
		handler = slog.NewTextHandler(out, opts)
	default:
		return nil, fmt.Errorf("invalid log format: %q", cfg.Format)
	}

	return slog.New(handler), nil
}

// parseLevel takes a standard level string and converts to the format slog expects
func parseLevel(s string) (slog.Level, error) {
	switch strings.ToLower(s) {
	case "debug":
		return slog.LevelDebug, nil
	case "info":
		return slog.LevelInfo, nil
	case "warn", "warning":
		return slog.LevelWarn, nil
	case "error":
		return slog.LevelError, nil
	default:
		return 0, fmt.Errorf("logging.level %q invalid (want debug|info|warn|error)", s)
	}
}

// List of secret names
var secretKeys = map[string]struct{}{
	"secret":   {},
	"password": {},
}

// redactSecrets removes secrets from logs where possible
func redactSecrets(_ []string, a slog.Attr) slog.Attr {
	if _, isSecret := secretKeys[strings.ToLower(a.Key)]; isSecret {
		return slog.Attr{Key: a.Key, Value: slog.StringValue("[REDACTED]")}
	}
	return a
}
