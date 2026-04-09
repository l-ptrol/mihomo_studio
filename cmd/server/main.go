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

	"github.com/l-ptrol/mhstudio-go/internal/config"
	"github.com/l-ptrol/mhstudio-go/internal/handler"
	"github.com/l-ptrol/mhstudio-go/web"
)

var Version = "2.2.62"

func main() {
	fStart := flag.Bool("start", false, "Start the server")
	fStop := flag.Bool("stop", false, "Stop the server")
	fRestart := flag.Bool("restart", false, "Restart the server")
	fUpdate := flag.Bool("update", false, "Update the service")
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
		startServer()
		return
	}

	if *fUpdate {
		fmt.Println(">>> Starting update...")
		if runtime.GOOS == "windows" {
			fmt.Println("Error: Update via script is not supported on Windows. Please update manually.")
		} else {
			runCmd("wget -O - https://raw.githubusercontent.com/l-ptrol/mihomo_studio/test-go/install.sh | sh")
		}
		return
	}

	// Default or explicit -start
	if *fStart || len(os.Args) == 1 {
		startServer()
	} else {
		flag.Usage()
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

