package web

import "embed"

//go:embed templates/index.html
var TemplateFS embed.FS
