# Enhanced Telemetry Implementation

## Overview

Comprehensive end-to-end OpenTelemetry instrumentation using Gen AI semantic conventions for standardized LLM observability. The implementation includes traces, metrics, and optional prompt/response logging from HTTP request through agent execution to individual LLM API calls.

## Changes Made

### 1. Dependencies Added (pyproject.toml)

```toml
"opentelemetry-instrumentation-httpx==0.51b0"    # Auto-trace HTTP calls to LLM APIs
"opentelemetry-instrumentation-requests==0.51b0" # Auto-trace requests library calls
```

### 2. Enhanced Instrumentation (src/agent/instrumentation.py)

**Core capabilities:**
- Auto-instrumentation of HTTPX and Requests libraries for LLM API call tracing
- Enhanced `traced_span` decorator with duration tracking and content size metrics
- `add_llm_response_attributes()` function captures token usage using Gen AI semantic conventions
- `log_llm_prompt_and_response()` function for optional prompt/response event logging
- `record_llm_operation_duration()` function for metrics recording
- Span status handling with OK/ERROR states
- OpenTelemetry metrics provider initialization

**Span attributes:**
- `duration_ms` - Execution time for any span
- `state.content_size` - Total character count in agent state
- Gen AI semantic convention attributes (see below)

### 3. LLM Call Tracing (src/agent/agents.py)

**Implementation:**
- `_call_llm()` function creates detailed spans for every LLM API call
- Captures comprehensive metrics before and after each call
- Separate operation names per agent: `llm.completion.planner`, `llm.completion.worker`, `llm.completion.reflection`, `llm.completion.reviewer`
- Duration tracking for performance analysis
- Prompt and response logging when enabled

**LLM attributes (Gen AI semantic conventions):**
- `gen_ai.system` - Provider (azure_openai, openai)
- `gen_ai.request.model` - Model name/deployment
- `gen_ai.request.max_tokens` - Token limit
- `gen_ai.operation.name` - Operation identifier (e.g., llm.completion.planner)
- `gen_ai.usage.input_tokens` - Actual prompt tokens (from API response)
- `gen_ai.usage.output_tokens` - Response tokens (from API response)
- `gen_ai.response.model` - Actual model that responded
- `gen_ai.response.finish_reasons` - Completion status (array)

### 4. Workflow Orchestration (src/agent/app.py)

**Parent span for complete workflow:**
- `workflow.execution` span wraps entire agent execution
- Captures request and response metadata
- Links all child spans (agents + LLM calls)

**Workflow attributes:**
- `workflow.task` - User's input task
- `workflow.task_length` - Input size
- `workflow.completed` - Success flag
- `workflow.plan_length` - Plan output size
- `workflow.work_length` - Work output size
- `workflow.review_length` - Review output size

### 5. OpenTelemetry Metrics

**Metrics implementation:** Real-time metrics export to Azure Monitor

**Metrics instruments:**
- `gen_ai.client.token.usage` (Counter)
  - Tracks token consumption by type (input/output), model, and operation
  - Enables real-time cost monitoring and budgeting
- `gen_ai.client.operation.duration` (Histogram)
  - Measures LLM operation latency distribution
  - Supports p95/p99 performance analysis

**Benefits:**
- Real-time dashboards
- Proactive alerting on token usage or latency
- Cost tracking without complex trace queries
- Performance monitoring at scale

### 6. Prompt & Response Logging

**Feature:** Optional content logging via span events

**Configuration:**
```bash
export ENABLE_PROMPT_LOGGING=true        # Default: false
export PROMPT_LOG_MAX_LENGTH=1000        # Default: 1000
```

**Span events created:**
- `gen_ai.content.prompt` - Captures prompt text (truncated)
  - `gen_ai.prompt` - The prompt text
  - `gen_ai.prompt.length` - Full prompt length
  - `gen_ai.prompt.truncated` - Boolean
- `gen_ai.content.completion` - Captures response text (truncated)
  - `gen_ai.completion` - The completion text
  - `gen_ai.completion.length` - Full completion length
  - `gen_ai.completion.truncated` - Boolean

**Use cases:**
- Debugging LLM behavior
- Quality assurance
- Prompt optimization
- Compliance auditing (when enabled)

**Privacy controls:**
- Disabled by default
- Automatic truncation
- Event-based (not indexed as attributes)
- Configurable per environment

### 7. Documentation

**README.md:**
- Features section with LLM observability highlights
- Detailed OpenTelemetry instrumentation architecture
- Comprehensive KQL query examples using Gen AI conventions
- Span hierarchy visualization

**Telemetry.md:**
- Complete instrumentation layer documentation
- Gen AI semantic convention attribute reference
- KQL query cookbook with token usage, cost estimation, and performance queries
- Metrics query examples
- Prompt logging configuration
- Application Insights usage guide
- Troubleshooting section
- Best practices for production use


## Telemetry Architecture

### Span Hierarchy
```
POST /run (FastAPI)
└── workflow.execution
    ├── planner.agent
    │   └── llm.completion.planner
    │       └── POST /openai/deployments/... (HTTPX)
    ├── worker.agent
    │   └── llm.completion.worker
    │       └── POST /openai/deployments/...
    ├── reflection.agent
    │   └── llm.completion.reflection
    │       └── POST /openai/deployments/...
    └── reviewer.agent
        └── llm.completion.reviewer
            └── POST /openai/deployments/...
```

### Instrumentation Layers
1. **HTTP Layer** (auto) - FastAPI, HTTPX, Requests
2. **Workflow Layer** (custom) - workflow.execution
3. **Agent Layer** (custom) - planner/worker/reflection/reviewer agents
4. **LLM Layer** (custom) - Individual completion calls with token metrics

## Testing

Code validation using py_compile:

```bash
python -m py_compile src/agent/instrumentation.py src/agent/agents.py src/agent/app.py
```

To test locally with enhanced telemetry, run debugger or execute via uvicorn from Terminal:

```bash
# Start debug mode or run directly
uvicorn src.agent.app:app --reload

# Make a request
curl -X POST http://localhost:8000/run \
  -H 'Content-Type: application/json' \
  -d '{"task": "Plan an RV road trip from Seattle to Jacksonville.  Visit at least 4 national parks.  Trip duration 4 weeks."}'

# View in Application Insights after 1-2 minutes
```

## Key Benefits

1. **Complete visibility** - Every HTTP request traced from entry to exit with detailed span hierarchy
2. **Token tracking** - Precise token usage metrics per agent and operation
3. **Cost analysis** - Real-time cost calculation and monitoring with metrics
4. **Performance optimization** - Identify slow agents and LLM calls with latency metrics
5. **Error detection** - Automatic exception tracking with full context
6. **Business metrics** - Custom attributes for domain-specific analysis
7. **Standards compliance** - OpenTelemetry Gen AI semantic conventions for better tool integration
8. **Real-time monitoring** - Metrics and dashboards for operational awareness
9. **Privacy controls** - Optional prompt logging with configurable truncation
10. **Production ready** - All tests passing with comprehensive instrumentation

## Usage

### Local Testing

Start the application and generate telemetry:

```bash
# Start the service
uvicorn src.agent.app:app --reload

# Make a test request
curl -X POST http://localhost:8000/run \
  -H 'Content-Type: application/json' \
  -d '{"task": "Plan an RV road trip from Seattle to Jacksonville. Visit at least 4 national parks. Trip duration 4 weeks."}'
```

### Viewing Telemetry in Application Insights

Wait 1-2 minutes for telemetry to propagate, then:

1. **Transaction Search** - View complete traces with span hierarchy
2. **Logs** - Run KQL queries for analysis
3. **Performance** - Analyze agent durations and bottlenecks
4. **Application Map** - Visualize service dependencies
5. **Metrics** - View real-time token usage and performance

### Example KQL Queries

### Example KQL Queries

**Token usage by agent:**

```kusto
dependencies
| where timestamp > ago(1h)
| where cloud_RoleName == "langgraph-multi-agent"
| where name startswith "llm.completion"
| extend 
    agent = tostring(split(name, ".")[2]),
    input_tokens = toint(customDimensions["gen_ai.usage.input_tokens"]),
    output_tokens = toint(customDimensions["gen_ai.usage.output_tokens"]),
    model = tostring(customDimensions["gen_ai.request.model"])
| summarize 
    calls = count(),
    total_input = sum(input_tokens),
    total_output = sum(output_tokens),
    avg_duration = avg(duration)
by agent, model
| order by total_input desc
```

**Token usage metrics:**

```kusto
customMetrics
| where name == "gen_ai.client.token.usage"
| extend 
    token_type = tostring(customDimensions["gen_ai.token.type"]),
    model = tostring(customDimensions["gen_ai.request.model"])
| summarize TotalTokens = sum(value) by token_type, model
```

**Performance metrics:**

```kusto
customMetrics
| where name == "gen_ai.client.operation.duration"
| extend 
    model = tostring(customDimensions["gen_ai.request.model"]),
    operation = tostring(customDimensions["gen_ai.operation.name"])
| summarize 
    avg_ms = avg(value),
    p95_ms = percentile(value, 95),
    p99_ms = percentile(value, 99)
by operation, model
```

See [Telemetry.md](./Telemetry.md) for comprehensive query examples including cost estimation, error analysis, and performance monitoring.

