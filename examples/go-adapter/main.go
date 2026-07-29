// Command go-adapter is a minimal Cursor SDK bridge adapter: it spawns the
// bridge, performs the ready-line handshake, authenticates with the bearer
// token, runs one local agent turn, and streams the response to stdout.
//
// See ../../docs/protocol.md for the lifecycle this implements.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"connectrpc.com/connect"

	sdkv1 "github.com/cursor/sdk-bridge/examples/go-adapter/gen/sdk/v1"
	"github.com/cursor/sdk-bridge/examples/go-adapter/gen/sdk/v1/sdkv1connect"
)

const (
	readyLinePrefix = "cursor-sdk-bridge ready "
	startupTimeout  = 30 * time.Second
	shutdownTimeout = 5 * time.Second
)

// discovery is the JSON payload after the ready-line prefix. Unknown fields
// are forward-compatible additions and are ignored.
type discovery struct {
	SchemaVersion int    `json:"schemaVersion"`
	ServerVersion string `json:"serverVersion"`
	Transport     string `json:"transport"`
	Protocol      string `json:"protocol"`
	URL           string `json:"url"`
	AuthTokenFile string `json:"authTokenFile"`
}

func main() {
	bridgeBin := flag.String("bridge", "", "path to the bridge launcher (default: $CURSOR_SDK_BRIDGE_BIN, then ./cursor-sdk-bridge/bin/cursor-sdk-bridge)")
	workspace := flag.String("workspace", ".", "workspace directory for the local agent")
	prompt := flag.String("prompt", "Say hello and name one file in this workspace.", "user message to send")
	model := flag.String("model", "", "model id (discovered via SdkCursorService.ListModels when empty)")
	flag.Parse()

	if os.Getenv("CURSOR_API_KEY") == "" {
		fatal("CURSOR_API_KEY must be set (create a key at https://cursor.com/dashboard)")
	}
	if err := run(*bridgeBin, *workspace, *prompt, *model); err != nil {
		fatal(err.Error())
	}
}

func run(bridgeBin, workspace, prompt, model string) error {
	workspace, err := filepath.Abs(workspace)
	if err != nil {
		return err
	}

	// --- 1. Spawn the bridge and wait for the ready line on stderr. ---
	cmd, disco, err := spawnBridge(bridgeBin, workspace)
	if err != nil {
		return err
	}
	defer func() {
		if cmd.Process != nil {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
		}
	}()

	// --- 2. Read the bearer token from authTokenFile. ---
	tokenBytes, err := os.ReadFile(disco.AuthTokenFile)
	if err != nil {
		return fmt.Errorf("read auth token file: %w", err)
	}
	token := strings.TrimSpace(string(tokenBytes))
	if token == "" {
		return errors.New("auth token file is empty")
	}

	// --- 3. Build authenticated Connect clients. ---
	httpClient := &http.Client{}
	auth := connect.WithInterceptors(bearerAuth(token))
	control := sdkv1connect.NewSdkBridgeControlServiceClient(httpClient, disco.URL, auth)
	agents := sdkv1connect.NewSdkAgentServiceClient(httpClient, disco.URL, auth)
	cursor := sdkv1connect.NewSdkCursorServiceClient(httpClient, disco.URL, auth)

	ctx := context.Background()

	// --- 4. Verify the connection. ---
	if _, err := control.Ping(ctx, connect.NewRequest(&sdkv1.PingRequest{})); err != nil {
		return fmt.Errorf("ping: %w", describeError(err))
	}
	version, err := control.GetVersion(ctx, connect.NewRequest(&sdkv1.GetVersionRequest{}))
	if err != nil {
		return fmt.Errorf("get version: %w", describeError(err))
	}
	fmt.Printf("ping ok; bridge %s protocol %s\n", version.Msg.GetBridgeVersion(), version.Msg.GetProtocolVersion())

	// --- 5. Create a local agent in the workspace. Local agents require an
	// explicit model; discover one via SdkCursorService when not given. ---
	if model == "" {
		// Catalog calls fail closed without an explicit api_key (no env
		// fallback), unlike agent operations.
		models, err := cursor.ListModels(ctx, connect.NewRequest(&sdkv1.ListModelsRequest{
			Options: &sdkv1.CursorRequestOptions{ApiKey: os.Getenv("CURSOR_API_KEY")},
		}))
		if err != nil {
			return fmt.Errorf("list models: %w", describeError(err))
		}
		if len(models.Msg.GetItems()) == 0 {
			return errors.New("no models available to this account")
		}
		model = models.Msg.GetItems()[0].GetId()
	}
	options := &sdkv1.AgentOptions{
		Model: &sdkv1.ModelSelection{Id: model},
		Local: &sdkv1.LocalAgentOptions{Cwd: []string{workspace}},
	}
	created, err := agents.CreateAgent(ctx, connect.NewRequest(&sdkv1.CreateAgentRequest{Options: options}))
	if err != nil {
		return fmt.Errorf("create agent: %w", describeError(err))
	}
	agentID := created.Msg.GetAgentId()
	fmt.Printf("agent created: %s (model %s)\n", agentID, created.Msg.GetModel().GetId())

	// --- 6. Send one message and stream the run. ---
	stream, err := agents.Send(ctx, connect.NewRequest(&sdkv1.SendRequest{
		AgentId: agentID,
		Message: &sdkv1.UserMessage{Text: prompt},
	}))
	if err != nil {
		return fmt.Errorf("send: %w", describeError(err))
	}
	for stream.Receive() {
		printStreamMessage(stream.Msg())
	}
	if err := stream.Err(); err != nil {
		return fmt.Errorf("run stream: %w", describeError(err))
	}

	// --- 7. Graceful shutdown: Shutdown RPC, then wait, then kill. ---
	_, _ = control.Shutdown(ctx, connect.NewRequest(&sdkv1.ShutdownRequest{}))
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case <-done:
	case <-time.After(shutdownTimeout):
		_ = cmd.Process.Kill()
		<-done
	}
	cmd.Process = nil
	fmt.Println("bridge stopped")
	return nil
}

// spawnBridge starts the launcher and scans stderr for the ready line.
func spawnBridge(bridgeBin, workspace string) (*exec.Cmd, *discovery, error) {
	if bridgeBin == "" {
		bridgeBin = os.Getenv("CURSOR_SDK_BRIDGE_BIN")
	}
	if bridgeBin == "" {
		bridgeBin = "./cursor-sdk-bridge/bin/cursor-sdk-bridge"
	}

	cmd := exec.Command(bridgeBin, "--workspace", workspace)
	cmd.Env = append(os.Environ(), "CURSOR_SDK_CLIENT_LANGUAGE=go")
	cmd.Stdout = os.Stdout
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, nil, fmt.Errorf("start %s: %w", bridgeBin, err)
	}

	type result struct {
		disco *discovery
		err   error
	}
	found := make(chan result, 1)
	go func() {
		scanner := bufio.NewScanner(stderr)
		scanner.Buffer(make([]byte, 4096), 1024*1024)
		var diagnostics strings.Builder
		for scanner.Scan() {
			line := scanner.Text()
			if !strings.HasPrefix(line, readyLinePrefix) {
				// Ordinary bridge diagnostics; keep for error reporting.
				fmt.Fprintln(&diagnostics, line)
				continue
			}
			var disco discovery
			if err := json.Unmarshal([]byte(strings.TrimPrefix(line, readyLinePrefix)), &disco); err != nil {
				found <- result{err: fmt.Errorf("invalid discovery JSON: %w", err)}
				return
			}
			found <- result{disco: &disco}
			// Keep draining stderr so the bridge never blocks on a full pipe.
			for scanner.Scan() {
			}
			return
		}
		found <- result{err: fmt.Errorf("bridge exited before emitting discovery: %s", strings.TrimSpace(diagnostics.String()))}
	}()

	select {
	case res := <-found:
		if res.err != nil {
			_ = cmd.Process.Kill()
			return nil, nil, res.err
		}
		disco := res.disco
		if disco.SchemaVersion != 1 || disco.Transport != "tcp" || disco.Protocol != "connect" {
			_ = cmd.Process.Kill()
			return nil, nil, fmt.Errorf("unsupported bridge discovery: schema=%d transport=%q protocol=%q",
				disco.SchemaVersion, disco.Transport, disco.Protocol)
		}
		fmt.Printf("bridge ready: url=%s serverVersion=%s\n", disco.URL, disco.ServerVersion)
		return cmd, disco, nil
	case <-time.After(startupTimeout):
		_ = cmd.Process.Kill()
		return nil, nil, fmt.Errorf("timed out after %s waiting for the bridge ready line", startupTimeout)
	}
}

// bearerAuth adds `Authorization: Bearer <token>` to every RPC — unary and
// streaming. Without it the bridge rejects requests with UNAUTHENTICATED.
type bearerAuth string

func (token bearerAuth) WrapUnary(next connect.UnaryFunc) connect.UnaryFunc {
	return func(ctx context.Context, req connect.AnyRequest) (connect.AnyResponse, error) {
		req.Header().Set("Authorization", "Bearer "+string(token))
		return next(ctx, req)
	}
}

func (token bearerAuth) WrapStreamingClient(next connect.StreamingClientFunc) connect.StreamingClientFunc {
	return func(ctx context.Context, spec connect.Spec) connect.StreamingClientConn {
		conn := next(ctx, spec)
		conn.RequestHeader().Set("Authorization", "Bearer "+string(token))
		return conn
	}
}

func (bearerAuth) WrapStreamingHandler(next connect.StreamingHandlerFunc) connect.StreamingHandlerFunc {
	return next
}

// printStreamMessage renders one RunStreamMessage. Messages with no envelope
// case set are keepalives and must be ignored (see docs/streaming.md).
func printStreamMessage(msg *sdkv1.RunStreamMessage) {
	switch envelope := msg.GetEnvelope().(type) {
	case *sdkv1.RunStreamMessage_SdkMessage:
		printSdkMessage(envelope.SdkMessage)
	case *sdkv1.RunStreamMessage_Result:
		fmt.Printf("run finished: status=%s\n",
			strings.TrimPrefix(envelope.Result.GetStatus().String(), "RUN_LIFECYCLE_STATUS_"))
		if code := envelope.Result.GetErrorCode(); code != "" {
			fmt.Printf("error code: %s\n", code)
		}
		if text := envelope.Result.GetResult().GetResult(); text != "" {
			fmt.Printf("final result:\n%s\n", text)
		}
	case *sdkv1.RunStreamMessage_Done:
		// End-of-stream marker; the stream closes normally afterwards.
	case nil:
		// Keepalive frame (empty envelope): ignore.
	default:
		// Unknown envelope case from a newer bridge: ignore.
	}
}

func printSdkMessage(message *sdkv1.SdkMessage) {
	payload := message.GetMessage().AsMap()
	switch message.GetType() {
	case "system":
		if runID, ok := payload["run_id"].(string); ok {
			fmt.Printf("[system] run started: %s\n", runID)
		}
	case "assistant":
		for _, text := range assistantTextBlocks(payload) {
			fmt.Printf("[assistant] %s\n", text)
		}
	case "tool_call":
		status, _ := payload["status"].(string)
		name, _ := payload["name"].(string)
		fmt.Printf("[tool_call %s] %s\n", status, name)
	default:
		fmt.Printf("[%s]\n", message.GetType())
	}
}

// assistantTextBlocks extracts text blocks from an SDK assistant message
// payload: {message: {content: [{type: "text", text: ...}, ...]}}.
func assistantTextBlocks(payload map[string]any) []string {
	inner, _ := payload["message"].(map[string]any)
	content, _ := inner["content"].([]any)
	var texts []string
	for _, block := range content {
		fields, _ := block.(map[string]any)
		if fields["type"] == "text" {
			if text, ok := fields["text"].(string); ok && text != "" {
				texts = append(texts, text)
			}
		}
	}
	return texts
}

// describeError appends the structured sdk.v1.SdkErrorDetails, when present,
// to a Connect error (see docs/errors.md).
func describeError(err error) error {
	var connectErr *connect.Error
	if !errors.As(err, &connectErr) {
		return err
	}
	for _, detail := range connectErr.Details() {
		value, valueErr := detail.Value()
		if valueErr != nil {
			continue
		}
		details, ok := value.(*sdkv1.SdkErrorDetails)
		if !ok {
			continue
		}
		return fmt.Errorf("%w (sdk_error_code=%s request_id=%s)",
			err,
			strings.TrimPrefix(details.GetSdkErrorCode().String(), "SDK_ERROR_CODE_"),
			details.GetRequestId())
	}
	return err
}

func fatal(message string) {
	fmt.Fprintln(os.Stderr, "error: "+message)
	os.Exit(1)
}
