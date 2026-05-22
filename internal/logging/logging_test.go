package logging

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"strings"
	"testing"

	"github.com/qsology/qsology/internal/config"
)

func TestNew_RedactsSecret(t *testing.T) {
	var buf bytes.Buffer
	logger, err := NewWith(config.LoggingConfig{Level: "info", Format: "json"}, &buf)
	if err != nil {
		t.Fatalf("NewWith failed: %v", err)
	}

	logger.Info("login attempt",
		"user", "alice",
		"password", "hunter2",
		"secret", "seatecastronomy", // toomanysecrets
	)

	var entry map[string]any
	if err := json.Unmarshal(buf.Bytes(), &entry); err != nil {
		t.Fatalf("log line is not JSON: %v\n%s", err, buf.String())
	}
	if entry["user"] != "alice" {
		t.Errorf("non-secret attr should pass, got %v", entry["user"])
	}
	for _, key := range []string{"password", "secret"} {
		if entry[key] != "[REDACTED]" {
			t.Errorf("%s should be [REDACTED], got %v", key, entry[key])
		}
	}
	for _, leaked := range []string{"hunter2", "seatecastronomy"} {
		if strings.Contains(buf.String(), leaked) {
			t.Errorf("plaintext secret %q leaked: %s", leaked, buf.String())
		}
	}
}

func TestNew_InvalidLevel(t *testing.T) {
	if _, err := NewWith(config.LoggingConfig{Level: "loud", Format: "json"}, nil); err == nil {
		t.Error("invalid level should error")
	}
}

func TestNew_InvalidFormat(t *testing.T) {
	if _, err := NewWith(config.LoggingConfig{Level: "info", Format: "yaml"}, nil); err == nil {
		t.Error("invalid format should error")
	}
}

func TestNew_LevelGating(t *testing.T) {
	var buf bytes.Buffer
	logger, err := NewWith(config.LoggingConfig{Level: "warn", Format: "json"}, &buf)
	if err != nil {
		t.Fatalf("NewWith failed: %v", err)
	}
	logger.Info("should not appear")
	logger.Warn("should appear")
	if !strings.Contains(buf.String(), "should appear") {
		t.Errorf("warn message missing")
	}
	if strings.Contains(buf.String(), "should not appear") {
		t.Errorf("info message leaked past warn threshold")
	}
}

func TestNew_DoesNotRedactBenignKeys(t *testing.T) {
	var buf bytes.Buffer
	logger, err := NewWith(config.LoggingConfig{Level: "info", Format: "json"}, &buf)
	if err != nil {
		t.Fatalf("NewWith failed: %v", err)
	}
	logger.LogAttrs(context.Background(), slog.LevelInfo, "msg",
		slog.String("username", "alice"),
		slog.String("ip", "1.2.3.4"),
	)
	if strings.Contains(buf.String(), "REDACTED") {
		t.Errorf("benign keys should not be redacted: %s", buf.String())
	}
}

func TestNew_TextFormat(t *testing.T) {
	var buf bytes.Buffer
	logger, err := NewWith(config.LoggingConfig{Level: "info", Format: "text"}, &buf)
	if err != nil {
		t.Fatalf("NewWith failed: %v", err)
	}
	logger.Info("hello")
	if !strings.Contains(buf.String(), "msg=hello") {
		t.Errorf("text format should contain msg=hello, got %s", buf.String())
	}
}
