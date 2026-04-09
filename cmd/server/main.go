package main

import (
	"flag"
	"fmt"
	"io/ioutil"
	"log"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/l-ptrol/mhstudio-go/internal/config"
	"github.com/l-ptrol/mhstudio-go/internal/handler"
	"github.com/l-ptrol/mhstudio-go/web"
)

var Version = "2.2.85"

func main() {
	fStart := flag.Bool("start", false, "Start the server")
	fStop := flag.Bool("stop", false, "Stop the server")
	fRestart := flag.Bool("restart", false, "Restart the server")
	fUpdate := flag.Bool("update", false, "Update the service")
	fUninstall := flag.Bool("uninstall", false, "Uninstall the service")
	fVersion := flag.Bool("v", false, "Show version")
	flag.Parse()

	if *fVersion || (len(os.Args) > 1 && os.Args[1] == "-version") {
		fmt.Printf("Mihomo Studio v%s\n", Version)
		return
	}

	if *fStop {
		stopService()
		return
	}

	if *fRestart {
		stopService()
		maybeDaemonize(Version)
		startServer()
		return
	}

	if *fUpdate {
		fmt.Println(">>> Starting update...")
		if runtime.GOOS == "windows" {
			fmt.Println("Error: Update via script is not supported on Windows. Please update manually.")
		} else {
			runCmd(fmt.Sprintf("wget -O - https://raw.githubusercontent.com/l-ptrol/mihomo_studio/test-go/install.sh?t=%d | sh", time.Now().Unix()))
		}
		return
	}

	if *fUninstall {
		uninstallService()
		return
	}

	// Default or explicit -start
	if *fStart || len(os.Args) == 1 {
		maybeDaemonize(Version)
		startServer()
	} else {
		flag.Usage()
	}
}

func maybeDaemonize(version string) {
	if os.Getenv("MHSTUDIO_DAEMON") != "1" && runtime.GOOS != "windows" {
		cmd := exec.Command(os.Args[0], os.Args[1:]...)
		cmd.Env = append(os.Environ(), "MHSTUDIO_DAEMON=1")
		cmd.Stdout = nil
		cmd.Stderr = nil
		cmd.Start()
		// Не выводим версию здесь, чтобы соответствовать запросу пользователя
		if strings.Contains(strings.Join(os.Args, " "), "-restart") {
			// При рестарте Stop уже выведен, просто подтверждаем запуск
			fmt.Println("Starting Mihomo Studio (Go)...")
		} else {
			fmt.Println("Starting Mihomo Studio (Go)...")
		}
		os.Exit(0)
	}
}

func startServer() {
	config.Init()
	// Kill potentially hanging process on our port before starting
	killProcessByPort(config.Port)

	tmplData, err := web.ContentFS.ReadFile("templates/index.html")
	if err != nil {
		log.Fatalf("Ошибка: %v", err)
	}

	h := handler.New(Version, web.ContentFS)
	h.LoadTemplate(string(tmplData))

	addr := fmt.Sprintf(":%d", config.Port)
	srv := &http.Server{
		Addr:    addr,
		Handler: h,
	}

	// Write PID file
	err = ioutil.WriteFile(config.PidFile, []byte(strconv.Itoa(os.Getpid())), 0644)
	if err != nil {
		log.Printf("Предупреждение: не удалось записать PID файл: %v", err)
	}

	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh
		log.Println("Остановка сервера...")
		os.Remove(config.PidFile)
		srv.Close()
	}()

	log.Printf("Mihomo Studio v%s запущен на http://0.0.0.0%s", Version, addr)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("Ошибка сервера: %v", err)
	}
}

func killProcessByPort(port int) {
	if runtime.GOOS == "windows" {
		return
	}

	// Attempt to find PID as simply as possible for busybox/router environments
	// Method 1: fuser
	cmd := exec.Command("sh", "-c", fmt.Sprintf("fuser -k %d/tcp", port))
	if err := cmd.Run(); err == nil {
		fmt.Printf("Process using port %d was terminated (fuser).\n", port)
		return
	}

	// Method 2: lsof + kill
	cmdText := fmt.Sprintf("lsof -ti:%d | xargs kill -9", port)
	cmd = exec.Command("sh", "-c", cmdText)
	if err := cmd.Run(); err == nil {
		fmt.Printf("Process using port %d was terminated (lsof).\n", port)
		return
	}

	// If no process found or tools not available, we just continue
}

func stopService() {
	fmt.Println("Stopping Mihomo Studio...")
	// Try port-based first as requested
	config.Init()
	killProcessByPort(config.Port)

	// Still try PID file as fallback/cleanup
	data, err := ioutil.ReadFile(config.PidFile)
	if err == nil {
		pid, _ := strconv.Atoi(strings.TrimSpace(string(data)))
		process, err := os.FindProcess(pid)
		if err == nil {
			process.Signal(syscall.SIGTERM)
		}
		os.Remove(config.PidFile)
	}
}

func uninstallService() {
	config.Init()
	fmt.Println(">>> Удаление Mihomo Studio...")
	stopService()

	// Удаляем бинарник
	progPath := "/opt/bin/mhstudio"
	if _, err := os.Stat(progPath); err == nil {
		os.Remove(progPath)
		fmt.Println("[-] Бинарный файл удален.")
	}

	// Удаляем init-скрипт
	initPath := "/opt/etc/init.d/S95mihomo-web"
	if _, err := os.Stat(initPath); err == nil {
		os.Remove(initPath)
		fmt.Println("[-] Скрипт инициализации удален.")
	}

	// Спрашиваем про конфиги
	fmt.Print("Удалить конфигурационные файлы и профили (/opt/etc/mihomo)? [y/N]: ")
	var resp string
	fmt.Scanln(&resp)
	if strings.ToLower(resp) == "y" {
		os.RemoveAll("/opt/etc/mihomo")
		fmt.Println("[-] Конфигурации и профили удалены.")
	}

	fmt.Println(">>> Удаление завершено.")
}

func runCmd(c string) {
	var cmd *exec.Cmd
	if runtime.GOOS == "windows" {
		cmd = exec.Command("cmd", "/C", c)
	} else {
		cmd = exec.Command("sh", "-c", c)
	}
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Run()
}

