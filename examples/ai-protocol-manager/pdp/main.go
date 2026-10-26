// A model routing PDP for configs/06-model-routing.yaml, as an ext_proc server.
//
// Clients ask for "smart" or "fast". "smart" is Claude while the tenant has budget left, and
// "fast" after that; "fast" is Gemini. The decision leaves as x-ai-* request headers, with the
// backend's credential. The tenant pays what the backend reported, read from the
// envoy.ai.token_usage record that arrives on the response trailers.
package main

import (
	"encoding/json"
	"flag"
	"io"
	"log"
	"net"
	"os"
	"sync"

	corev3 "github.com/envoyproxy/go-control-plane/envoy/config/core/v3"
	extprocv3 "github.com/envoyproxy/go-control-plane/envoy/service/ext_proc/v3"
	typev3 "github.com/envoyproxy/go-control-plane/envoy/type/v3"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/encoding/protowire"
)

type backend struct {
	api, binding, model, host string
	credentials               map[string]string
}

var backends = map[string]backend{
	"smart": {
		api:     "ANTHROPIC_MESSAGES",
		binding: "/v1/messages",
		model:   "claude-sonnet-4-5",
		host:    "api.anthropic.com",
		credentials: map[string]string{
			"x-api-key":         os.Getenv("ANTHROPIC_API_KEY"),
			"anthropic-version": "2023-06-01",
		},
	},
	"fast": {
		api:         "GEMINI_GENERATE_CONTENT",
		binding:     "/v1beta/models/{model}:{generateContent|streamGenerateContent?alt=sse}",
		model:       "gemini-2.5-flash",
		host:        "generativelanguage.googleapis.com",
		credentials: map[string]string{"x-goog-api-key": os.Getenv("GEMINI_API_KEY")},
	},
}

type server struct {
	extprocv3.UnimplementedExternalProcessorServer
	budget     uint64
	hostSuffix string

	mu    sync.Mutex
	spent map[string]uint64
}

func (s *server) Process(stream extprocv3.ExternalProcessor_ProcessServer) error {
	tenant := "anonymous"
	for {
		req, err := stream.Recv()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		var resp *extprocv3.ProcessingResponse
		switch r := req.Request.(type) {
		case *extprocv3.ProcessingRequest_RequestHeaders:
			for _, h := range r.RequestHeaders.GetHeaders().GetHeaders() {
				if h.Key == "x-tenant" {
					tenant = string(h.RawValue)
				}
			}
			resp = s.decide(tenant, req)
		case *extprocv3.ProcessingRequest_ResponseTrailers:
			s.settle(tenant, req)
			resp = &extprocv3.ProcessingResponse{Response: &extprocv3.ProcessingResponse_ResponseTrailers{
				ResponseTrailers: &extprocv3.TrailersResponse{},
			}}
		default:
			log.Printf("unexpected message %T", r)
			continue
		}
		if err := stream.Send(resp); err != nil {
			return err
		}
	}
}

// decide reads the request's description, which the AI Protocol Manager published before the
// headers reached ext_proc.
func (s *server) decide(tenant string, req *extprocv3.ProcessingRequest) *extprocv3.ProcessingResponse {
	info := req.GetMetadataContext().GetTypedFilterMetadata()["envoy.ai.request_info"].GetValue()
	model, estimate := stringField(info, 2), uint64Field(info, 7)

	s.mu.Lock()
	spent := s.spent[tenant]
	s.mu.Unlock()
	choice := model
	if choice == "smart" && spent >= s.budget {
		choice = "fast"
	}
	b, ok := backends[choice]
	record("decide", map[string]any{"tenant": tenant, "model": model, "estimated_input_tokens": estimate,
		"spent": spent, "backend": b.api, "backend_model": b.model})
	if !ok {
		return &extprocv3.ProcessingResponse{Response: &extprocv3.ProcessingResponse_ImmediateResponse{
			ImmediateResponse: &extprocv3.ImmediateResponse{
				Status: &typev3.HttpStatus{Code: typev3.StatusCode_BadRequest},
				Body:   []byte(`{"error":"unknown model, use smart or fast"}`),
			},
		}}
	}

	set := []*corev3.HeaderValueOption{
		header("x-ai-api", b.api),
		header("x-ai-binding", b.binding),
		header("x-ai-model", b.model),
		header("x-ai-host", b.host+s.hostSuffix),
	}
	for k, v := range b.credentials {
		set = append(set, header(k, v))
	}
	return &extprocv3.ProcessingResponse{Response: &extprocv3.ProcessingResponse_RequestHeaders{
		RequestHeaders: &extprocv3.HeadersResponse{Response: &extprocv3.CommonResponse{
			HeaderMutation: &extprocv3.HeaderMutation{SetHeaders: set},
		}},
	}}
}

func (s *server) settle(tenant string, req *extprocv3.ProcessingRequest) {
	usage, ok := req.GetMetadataContext().GetTypedFilterMetadata()["envoy.ai.token_usage"]
	if !ok {
		record("usage", map[string]any{"tenant": tenant, "missing": true})
		return
	}
	total := uint64Field(usage.Value, 5)
	s.mu.Lock()
	s.spent[tenant] += total
	spent := s.spent[tenant]
	s.mu.Unlock()
	record("usage", map[string]any{"tenant": tenant, "model": stringField(usage.Value, 2),
		"input": uint64Field(usage.Value, 3), "output": uint64Field(usage.Value, 4), "total": total,
		"spent": spent})
}

func record(event string, fields map[string]any) {
	fields["event"] = event
	line, _ := json.Marshal(fields)
	os.Stdout.Write(append(line, '\n'))
}

func header(key, value string) *corev3.HeaderValueOption {
	return &corev3.HeaderValueOption{
		Header:       &corev3.HeaderValue{Key: key, RawValue: []byte(value)},
		AppendAction: corev3.HeaderValueOption_OVERWRITE_IF_EXISTS_OR_ADD,
	}
}

// envoy.data.ai.v3 records are read off the wire, so the PDP needs no protos newer than
// go-control-plane's.
func field(msg []byte, want protowire.Number) []byte {
	for len(msg) > 0 {
		num, typ, n := protowire.ConsumeTag(msg)
		if n < 0 {
			return nil
		}
		msg = msg[n:]
		m := protowire.ConsumeFieldValue(num, typ, msg)
		if m < 0 {
			return nil
		}
		if num == want && typ == protowire.BytesType {
			v, _ := protowire.ConsumeBytes(msg)
			return v
		}
		msg = msg[m:]
	}
	return nil
}

// A google.protobuf.UInt64Value field; 0 when absent.
func uint64Field(msg []byte, num protowire.Number) uint64 {
	wrapper := field(msg, num)
	n, typ, l := protowire.ConsumeTag(wrapper)
	if l < 0 || n != 1 || typ != protowire.VarintType {
		return 0
	}
	v, _ := protowire.ConsumeVarint(wrapper[l:])
	return v
}

func stringField(msg []byte, num protowire.Number) string { return string(field(msg, num)) }

func main() {
	addr := flag.String("addr", "127.0.0.1:50051", "listen address")
	budget := flag.Uint64("budget", 1000, "tokens a tenant may spend on Claude")
	hostSuffix := flag.String("host-suffix", "", "appended to each backend host, e.g. :8443")
	flag.Parse()

	lis, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatal(err)
	}
	g := grpc.NewServer()
	extprocv3.RegisterExternalProcessorServer(g, &server{
		budget: *budget, hostSuffix: *hostSuffix, spent: map[string]uint64{},
	})
	log.Printf("pdp on %s", lis.Addr())
	log.Fatal(g.Serve(lis))
}
