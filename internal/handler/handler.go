package handler

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"
	"io/fs"

	"github.com/l-ptrol/mhstudio-go/internal/backup"
	"github.com/l-ptrol/mhstudio-go/internal/config"
	"github.com/l-ptrol/mhstudio-go/internal/parser"
	"github.com/l-ptrol/mhstudio-go/internal/profile"
	"github.com/l-ptrol/mhstudio-go/internal/proxy"
)

type Handler struct {
	template string
	Version  string
	files    fs.FS
}

func New(version string, files fs.FS) *Handler {
	return &Handler{Version: version, files: files}
}

func (h *Handler) LoadTemplate(tmpl string) {
	h.template = tmpl
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// No-cache headers
	w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate")
	w.Header().Set("Pragma", "no-cache")
	w.Header().Set("Expires", "0")

	if strings.HasPrefix(r.URL.Path, "/mihomo_panel/") {
		h.proxyPass(w, r)
		return
	}

	if strings.HasPrefix(r.URL.Path, "/static/") {
		http.FileServer(http.FS(h.files)).ServeHTTP(w, r)
		return
	}

	switch r.Method {
	case http.MethodGet:
		if r.URL.Path != "/" {
			http.Error(w, "Not Found", 404)
			return
		}
		h.handleGet(w, r)
	case http.MethodPost:
		if r.URL.Path != "/" {
			http.Error(w, "Not Found", 404)
			return
		}
		h.handlePost(w, r)
	default:
		if strings.HasPrefix(r.URL.Path, "/mihomo_panel/") {
			h.proxyPass(w, r)
			return
		}
		http.Error(w, "Method Not Allowed", 405)
	}
}

func (h *Handler) handleGet(w http.ResponseWriter, r *http.Request) {
	content, _ := os.ReadFile(config.ConfigPath)
	if len(content) == 0 {
		content = []byte("proxies:\n")
	}

	backupsHTML := h.renderBackups()
	profilesOpts := h.renderProfileOptions()
	now := time.Now().Format("15:04:05")

	out := h.template
	out = strings.Replace(out, "__JSON_CONTENT__", string(content), 1)
	out = strings.Replace(out, "__BACKUPS__", backupsHTML, 1)
	out = strings.Replace(out, "__PROFILES__", profilesOpts, 1)
	out = strings.Replace(out, "__TIME__", now, 1)

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(200)
	w.Write([]byte(out))
}

func (h *Handler) handlePost(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	r.Body.Close()

	params := parseFormBody(string(body))
	act := params["act"]

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(200)

	switch act {
	case "switch_prof":
		h.handleSwitchProf(w, params)
	case "add_prof":
		h.handleAddProf(w, params)
	case "del_prof":
		h.handleDelProf(w, params)
	case "get_prof_content":
		h.handleGetProfContent(w, params)
	case "rename_proxy":
		h.handleRenameProxy(w, params)
	case "parse":
		h.handleParseVLESS(w, params)
	case "add_wireguard":
		h.handleAddWireGuard(w, params)
	case "apply_insert":
		h.handleApplyInsert(w, params)
	case "replace_proxy":
		h.handleReplaceProxy(w, params)
	case "clean_backups":
		h.handleCleanBackups(w, params)
	case "del_backup":
		h.handleDelBackup(w, params)
	case "rest":
		h.handleRestoreBackup(w, params)
	case "view_backup":
		h.handleViewBackup(w, params)
	case "update_service":
		h.handleUpdateService(w)
	case "check_update":
		h.handleCheckUpdate(w)
	case "restart_service":
		h.handleRestartService(w)
	case "save", "restart":
		h.handleSave(w, params, act == "restart")
	default:
		json.NewEncoder(w).Encode(map[string]string{"error": "Unknown action"})
	}
}

func (h *Handler) handleSwitchProf(w http.ResponseWriter, params map[string]string) {
	name := params["name"]
	if err := profile.SwitchProfile(name); err != nil {
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func (h *Handler) handleAddProf(w http.ResponseWriter, params map[string]string) {
	name := params["name"]
	content := params["content"]
	if err := profile.AddProfile(name, content); err != nil {
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func (h *Handler) handleDelProf(w http.ResponseWriter, params map[string]string) {
	name := params["name"]
	if err := profile.DeleteProfile(name); err != nil {
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func (h *Handler) handleGetProfContent(w http.ResponseWriter, params map[string]string) {
	name := params["name"]
	content, err := profile.GetProfileContent(name)
	if err != nil {
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	json.NewEncoder(w).Encode(map[string]string{"status": "ok", "content": content})
}

func (h *Handler) handleRenameProxy(w http.ResponseWriter, params map[string]string) {
	oldName := params["old_name"]
	newName := params["new_name"]
	content := params["content"]
	if oldName == "" || newName == "" || content == "" {
		json.NewEncoder(w).Encode(map[string]string{"error": "Missing parameters"})
		return
	}
	newContent := proxy.RenameProxy(content, oldName, newName)
	json.NewEncoder(w).Encode(map[string]string{"status": "ok", "new_content": newContent})
}

func (h *Handler) handleParseVLESS(w http.ResponseWriter, params map[string]string) {
	link := params["link"]
	customName := params["proxy_name"]
	result, err := parser.ParseVLESS(link, customName)
	if err != nil {
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	json.NewEncoder(w).Encode(result)
}

func (h *Handler) handleAddWireGuard(w http.ResponseWriter, params map[string]string) {
	configText := params["config_text"]
	customName := params["proxy_name"]
	if configText == "" {
		json.NewEncoder(w).Encode(map[string]string{"error": "Empty config"})
		return
	}
	result, err := parser.ParseWireGuard(configText, customName)
	if err != nil {
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	json.NewEncoder(w).Encode(result)
}

func (h *Handler) handleApplyInsert(w http.ResponseWriter, params map[string]string) {
	content := params["content"]
	proxyName := params["proxy_name"]
	proxyYAML := params["proxy_yaml"]
	targetsStr := params["targets"]

	var targets []string
	json.Unmarshal([]byte(targetsStr), &targets)

	newContent := proxy.InsertProxyLogic(content, proxyName, proxyYAML, targets)
	json.NewEncoder(w).Encode(map[string]string{"new_content": newContent})
}

func (h *Handler) handleReplaceProxy(w http.ResponseWriter, params map[string]string) {
	targetName := params["target_name"]
	newYAML := params["new_yaml"]
	content := params["content"]
	newContent := proxy.ReplaceProxyBlock(content, targetName, newYAML)
	json.NewEncoder(w).Encode(map[string]string{"new_content": newContent})
}

func (h *Handler) handleCleanBackups(w http.ResponseWriter, params map[string]string) {
	limit, _ := strconv.Atoi(params["limit"])
	if limit < 1 {
		limit = 5
	}
	backup.CleanBackups(limit)
	json.NewEncoder(w).Encode(map[string]string{"backups": h.renderBackups()})
}

func (h *Handler) handleDelBackup(w http.ResponseWriter, params map[string]string) {
	fname := params["f"]
	backup.DeleteBackup(fname)
	json.NewEncoder(w).Encode(map[string]string{"backups": h.renderBackups()})
}

func (h *Handler) handleRestoreBackup(w http.ResponseWriter, params map[string]string) {
	fname := params["f"]
	backup.RestoreBackup(fname)
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func (h *Handler) handleViewBackup(w http.ResponseWriter, params map[string]string) {
	fname := params["f"]
	content, err := backup.ViewBackup(fname)
	if err != nil {
		json.NewEncoder(w).Encode(map[string]string{"error": "File not found"})
		return
	}
	json.NewEncoder(w).Encode(map[string]string{"content": content})
}

func (h *Handler) handleUpdateService(w http.ResponseWriter) {
	json.NewEncoder(w).Encode(map[string]string{"status": "updating"})
	go func() {
		time.Sleep(500 * time.Millisecond)
		var cmd *exec.Cmd
		updateCmd := config.UpdateCmd + " > /tmp/mhstudio_update.log 2>&1"
		if runtime.GOOS == "windows" {
			cmd = exec.Command("cmd", "/C", updateCmd)
		} else {
			cmd = exec.Command("sh", "-c", updateCmd)
		}
		cmd.Run()
	}()
}

func (h *Handler) handleCheckUpdate(w http.ResponseWriter) {
	latest := h.GetLatestVersion()
	json.NewEncoder(w).Encode(map[string]string{
		"current": h.Version,
		"latest":  latest,
		"update":  strconv.FormatBool(latest != "" && latest != h.Version),
	})
}

func (h *Handler) GetLatestVersion() string {
	client := http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get("https://raw.githubusercontent.com/l-ptrol/mihomo_studio/test-go/cmd/server/main.go")
	if err != nil {
		return ""
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	content := string(body)

	re := regexp.MustCompile(`var Version = "([^"]+)"`)
	m := re.FindStringSubmatch(content)
	if len(m) > 1 {
		return m[1]
	}
	return ""
}

func (h *Handler) handleRestartService(w http.ResponseWriter) {
	json.NewEncoder(w).Encode(map[string]string{"status": "restarting"})
	go func() {
		time.Sleep(500 * time.Millisecond)
		if runtime.GOOS == "windows" {
			exec.Command("cmd", "/C", config.InitScript+" restart").Start()
		} else {
			exec.Command("sh", "-c", config.InitScript+" restart").Start()
		}
	}()
}

func (h *Handler) handleSave(w http.ResponseWriter, params map[string]string, doRestart bool) {
	newContent := strings.Replace(params["content"], "\r\n", "\n", -1)

	// Создаём бэкап
	backup.CreateBackup()

	// Сохраняем конфиг
	os.WriteFile(config.ConfigPath, []byte(newContent), 0644)

	if doRestart {
		var cmd *exec.Cmd
		if runtime.GOOS == "windows" {
			cmd = exec.Command("cmd", "/C", config.RestartCmd)
		} else {
			cmd = exec.Command("sh", "-c", config.RestartCmd)
		}
		cmd.Env = append(os.Environ(), "TERM=xterm-256color")
		cmd.Run()
		logData, _ := os.ReadFile(config.LogFile)
		log := string(logData)
		if log == "" {
			log = "Log empty"
		}
		json.NewEncoder(w).Encode(map[string]string{"log": log})
	} else {
		json.NewEncoder(w).Encode(map[string]string{
			"status":  "ok",
			"time":    time.Now().Format("15:04:05"),
			"backups": h.renderBackups(),
		})
	}
}

func (h *Handler) proxyPass(w http.ResponseWriter, r *http.Request) {
	panelPort := h.getPanelPort()
	if panelPort == "" {
		http.Error(w, "Panel port not found in config", 500)
		return
	}

	relPath := strings.TrimPrefix(r.URL.Path, "/mihomo_panel/")
	targetURL := fmt.Sprintf("http://127.0.0.1:%s/%s", panelPort, relPath)

	target, _ := url.Parse(targetURL)
	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.Director = func(req *http.Request) {
		req.URL = target
		req.Host = target.Host
		req.Header.Del("Origin")
		req.Header.Del("Referer")
	}
	proxy.ServeHTTP(w, r)
}

func (h *Handler) getPanelPort() string {
	data, err := os.ReadFile(config.ConfigPath)
	if err != nil {
		return ""
	}
	content := string(data)
	re := regexp.MustCompile(`external-controller:\s*(?:['"]?)(?:[^:]*):(\d+)(?:['"]?)`)
	m := re.FindStringSubmatch(content)
	if len(m) > 1 {
		return m[1]
	}
	return ""
}

func (h *Handler) renderBackups() string {
	backups, err := backup.ListBackups()
	if err != nil || len(backups) == 0 {
		return `<div style="color:var(--txt-sec);font-size:13px;text-align:center;padding:15px">Нет бэкапов</div>`
	}

	var b strings.Builder
	for _, bk := range backups {
		t := bk.ModTime.Format("02.01 15:04")
		b.WriteString(fmt.Sprintf(`<div class="bk-item">
			<div class="bk-info">
				<div class="bk-name">%s</div>
				<div class="bk-date">%s</div>
			</div>
			<div class="bk-btns">
				<button onclick="viewBackup('%s')" class="btn-icon btn-ghost" title="Просмотр">
					<svg class="ico" viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
				</button>
				<button onclick="restoreBackup('%s')" class="btn-icon btn-ghost" title="Восстановить">
					<svg class="ico" viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
				</button>
				<button onclick="delBackup('%s')" class="btn-icon btn-ghost" style="color:var(--danger)" title="Удалить">
					<svg class="ico" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
				</button>
			</div>
		   </div>`, bk.Name, t, bk.Name, bk.Name, bk.Name))
	}
	return b.String()
}

func (h *Handler) renderProfileOptions() string {
	current := profile.GetCurrentProfile()
	profiles, err := profile.ListProfiles()
	if err != nil {
		return ""
	}

	var opts strings.Builder
	for _, p := range profiles {
		sel := ""
		if p.Name == current {
			sel = "selected"
		}
		opts.WriteString(fmt.Sprintf(`<option value="%s" %s>%s</option>`, p.Name, sel, p.Name))
	}
	return opts.String()
}

func parseFormBody(body string) map[string]string {
	params := make(map[string]string)
	for _, pair := range strings.Split(body, "&") {
		parts := strings.SplitN(pair, "=", 2)
		if len(parts) == 2 {
			key, _ := url.QueryUnescape(parts[0])
			val, _ := url.QueryUnescape(parts[1])
			params[key] = val
		}
	}
	return params
}
