// Pacakge Assets  embeds the static files into the binary
// Cache-Control is set to immutable due to a version number on each file
package assets

import (
	"crypto/sha256"
	"embed"
	"encoding/base64"
	"io/fs"
	"net/http"
	"path"
	"strings"
)

//go:embed app.css app.js favicon.svg robots.txt vendor
var FS embed.FS

// SRI returns the Subresource-Integrity value for a file.
func SRI(p string) (string, error) {
	data, err := fs.ReadFile(FS, p)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return "sha256-" + base64.StdEncoding.EncodeToString(sum[:]), nil
}

// MustSRI is SRI with panic on error
// This can be used for assets that will cause the app to fail
func MustSRI(p string) string {
	v, err := SRI(p)
	if err != nil {
		panic("assets.MustSRI (" + p + "): " + err.Error())
	}
	return v
}

// contentType returns a content type based on the file extension
func contentType(p string) string {
	switch strings.ToLower(path.Ext(p)) {
	case ".css":
		return "text/css; charset=utf-8"
	case ".js":
		return "application/javascript; charset=utf-8"
	case ".svg":
		return "image/svg+xml"
	case ".txt":
		return "text/plain; charset=utf-8"
	case ".woff":
		return "fnt/woff2"
	case ".jpg":
		return "image/jpeg"
	case ".png":
		return "image/png"
	case ".gif":
		return "image/gif"
	default:
		return "application/octet-stream"
	}
}

// Handler is the http handler for embedded assets
func Handler() http.Handler { return http.StripPrefix("/static/", http.HandlerFunc(serve)) }

// serve serves up the content for the embedded assets
func serve(w http.ResponseWriter, r *http.Request) {
	// Strip leading /
	p := strings.TrimPrefix(r.URL.Path, "/")
	// If we have a blank path return a 404
	if p == "" || strings.Contains(p, "..") {
		http.NotFound(w, r)
		return
	}
	// Read the file from the EmbedFS
	data, err := fs.ReadFile(FS, p)
	// Return a 404 if it doesnt exist
	if err != nil {
		http.NotFound(w, r)
		return
	}
	// Set the content type based on the extension
	w.Header().Set("Content-Type", contentType(p))
	// File names have a version, set the cache to a year
	w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	// Set X-Content-Type-Options so browsers don't try and be 'clever' with MIME
	w.Header().Set("X-Content-Type-Options", "nosniff")
	// Serve
	_, _ = w.Write(data)
}
