# End-to-End Telemetry Guide

This document describes the comprehensive OpenTelemetry instrumentation in this LLM observability demo.

## Related Documentation

- [Enhanced Telemetry](./Enhanced_Telemetry.md) - Implementation details and architecture
- [Telemetry Tutorial](telemetry-tutorial-app-insights.md) - Step-by-step guide to viewing telemetry in Application Insights

**Note:** Log Analytics queries use different table names (AppRequests, AppDependencies) than Application Insights (requests, dependencies).

## Telemetry Architecture

### Instrumentation Layers

The application uses multiple layers of OpenTelemetry instrumentation to provide complete end-to-end observability:

#### 1. Auto-Instrumentation (Zero-code)
- **FastAPI** - Automatically traces all HTTP requests
- **HTTPX** - Traces all HTTP client calls (used by OpenAI SDK)
- **Requests** - Traces HTTP calls from any requests-based libraries

#### 2. Custom Agent Spans
Each agent in the workflow creates a custom span:
- `planner.agent` - Initial planning phase
- `worker.agent` - Execution phase
- `reflection.agent` - Quality validation
- `reviewer.agent` - Final review

#### 3. Workflow Orchestration Span
- `workflow.execution` - Parent span for entire multi-agent flow
- Links all agent executions under single operation

#### 4. LLM Call Instrumentation
Each LLM call creates a detailed span with rich attributes:
- `llm.completion.planner`
- `llm.completion.worker`
- `llm.completion.reflection`
- `llm.completion.reviewer`

## Captured Metrics

### LLM Call Attributes

The application uses OpenTelemetry Gen AI semantic conventions for standardized LLM observability. Every LLM API call captures:

| Attribute | Description | Example |
|-----------|-------------|---------|
| `gen_ai.system` | Provider type | `azure_openai`, `openai` |
| `gen_ai.request.model` | Model identifier | `gpt-4o-mini` |
| `gen_ai.request.max_tokens` | Token limit requested | `256` |
| `gen_ai.operation.name` | Operation identifier | `llm.completion.planner` |
| `gen_ai.usage.input_tokens` | Actual prompt tokens | `45` |
| `gen_ai.usage.output_tokens` | Response tokens | `128` |
| `gen_ai.response.model` | Actual model used | `gpt-4o-mini-2024-07-18` |
| `gen_ai.response.finish_reasons` | Why completion stopped (array) | `["stop"]`, `["length"]` |

### Agent Attributes

Each agent span captures:

| Attribute | Description |
|-----------|-------------|
| `state.keys` | Number of state keys |
| `state.content_size` | Total character count in state |
| `duration_ms` | Execution time in milliseconds |

### Workflow Attributes

The workflow span captures:

| Attribute | Description |
|-----------|-------------|
| `workflow.task` | User's input task |
| `workflow.task_length` | Task character count |
| `workflow.completed` | Success flag |
| `workflow.plan_length` | Generated plan size |
| `workflow.work_length` | Work output size |
| `workflow.review_length` | Review output size |

## Span Hierarchy

Complete trace for a single request:

```
POST /run                                    [FastAPI auto-instrumentation]
├── attributes: http.method, http.url, http.status_code
└── workflow.execution                       [Custom span]
    ├── attributes: workflow.task, workflow.completed
    ├── planner.agent                        [Custom span]
    │   ├── attributes: state.keys, duration_ms
    │   └── llm.completion.planner           [Custom span with LLM metrics]
    │       ├── attributes: gen_ai.request.model, gen_ai.usage.*, gen_ai.operation.name, duration
    │       └── POST /openai/deployments/... [HTTPX auto-instrumentation]
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

## KQL Queries for Analysis

The KQL queries below are formatted for App Insights.  If querying in Log Analytics, this table shows the table name differences:

| Log Analytics | App Insights |
|---------------|--------------|
| AppRequests |requests |
| TimeGenerated |timestamp |
| AppRoleName |cloud_RoleName |
| DurationMs |duration |
| Properties |customDimensions |

### Token Usage by Agent

```kusto
dependencies
| where timestamp > ago(24h)
| where cloud_RoleName == "langgraph-multi-agent"
| where name startswith "llm.completion"
| extend 
    agent = tostring(split(name, ".")[2]),
    input_tokens = toint(customDimensions["gen_ai.usage.input_tokens"]),
    output_tokens = toint(customDimensions["gen_ai.usage.output_tokens"]),
    model = tostring(customDimensions["gen_ai.request.model"])
| summarize 
    total_calls = count(),
    avg_input_tokens = avg(input_tokens),
    avg_output_tokens = avg(output_tokens),
    total_input_tokens = sum(input_tokens),
    total_output_tokens = sum(output_tokens),
    avg_duration_ms = avg(duration)
by agent, model
| order by total_input_tokens desc
```

### LLM Performance Percentiles

```kusto
dependencies
| where timestamp > ago(1h)
| where cloud_RoleName == "langgraph-multi-agent"
| where name startswith "llm.completion"
| extend agent = tostring(split(name, ".")[2])
| summarize 
    p50 = percentile(duration, 50),
    p95 = percentile(duration, 95),
    p99 = percentile(duration, 99),
    avg_duration = avg(duration)
by agent
| order by p95 desc
```

### Cost Estimation (tokens to dollars)

```kusto
// Example: GPT-4o-mini pricing
// Input: $0.150 per 1M tokens
// Output: $0.600 per 1M tokens
dependencies
| where timestamp > ago(24h)
| where cloud_RoleName == "langgraph-multi-agent"
| where name startswith "llm.completion"
| extend 
    input_tokens = toint(customDimensions["gen_ai.usage.input_tokens"]),
    output_tokens = toint(customDimensions["gen_ai.usage.output_tokens"]),
    model = tostring(customDimensions["gen_ai.request.model"])
| summarize 
    total_input_tokens = sum(input_tokens),
    total_output_tokens = sum(output_tokens),
    total_calls = count()
by model
| project model, total_calls, total_input_tokens, total_output_tokens,
          input_cost = round(total_input_tokens * 0.150 / 1000000.0, 4), 
          output_cost = round(total_output_tokens * 0.600 / 1000000.0, 4), 
          total_cost = round((total_input_tokens * 0.150 / 1000000.0) + (total_output_tokens * 0.600 / 1000000.0), 4)
```

### Error Rate by Agent

```kusto
dependencies
| where timestamp > ago(1h)
| where cloud_RoleName == "langgraph-multi-agent"
| where name startswith "llm.completion"
| extend 
    agent = tostring(split(name, ".")[2]),
    is_success = resultCode == "0" or success == true
| summarize 
    total = count(),
    successes = countif(is_success),
    failures = countif(not(is_success)),
    success_rate = countif(is_success) * 100.0 / count()
by agent
| order by success_rate asc
```

### End-to-End Request Latency Breakdown

```kusto
let OperationIds = requests
    | where timestamp > ago(1h)
    | where cloud_RoleName == "langgraph-multi-agent"
    | project operation_Id;
union traces, dependencies, requests
| where operation_Id in (OperationIds)
| project timestamp, itemType, name, duration, operation_Id
| order by operation_Id, timestamp
```

## Viewing in Application Insights

### Transaction Search
1. Navigate to **Application Insights |Transaction search**
2. Filter by time range
3. Look for `POST /run` requests
4. Click any request to see the complete span hierarchy
5. Expand each span to view attributes (token usage, durations, etc.)

### Application Map
1. Navigate to **Application Insights |Application Map**
2. View service dependencies and call patterns
3. See failure rates and average durations
4. Identify bottlenecks in the agent workflow

### Performance Blade
1. Navigate to **Application Insights |Performance**
2. View operation times for each agent
3. Analyze dependencies (LLM calls)
4. Drill into specific operations for detailed traces

### Live Metrics
1. Navigate to **Application Insights |Live Metrics**
2. Run a request: `curl -X POST http://localhost:8000/run -H 'Content-Type: application/json' -d '{"task": "Test"}'`
3. Watch real-time telemetry flow
4. See request rate, duration, dependencies as they happen

## Local Testing with In-Memory Exporter

For testing without Azure connection:

```bash
export OTEL_INMEMORY_EXPORTER=1
pytest -v test/test_api.py
```

This captures spans in memory for validation without requiring Application Insights.

## Troubleshooting

### No telemetry appearing in Azure

1. Check `APPINSIGHTS_CONNECTION_STRING` is set correctly
2. Verify spans are being created locally first (use in-memory exporter)
3. Check firewall allows outbound HTTPS to `*.applicationinsights.azure.com`
4. Telemetry batching may delay up to 60 seconds - wait and refresh

### Missing LLM metrics

1. Ensure `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` are set
2. Check `AZURE_OPENAI_DEPLOYMENT` matches your deployment name
3. Verify the OpenAI SDK version is >= 1.54.0
4. LLM attributes only appear when real API calls succeed

### Incomplete span hierarchy

1. Ensure all instrumentation packages are installed:
   - `opentelemetry-instrumentation-fastapi`
   - `opentelemetry-instrumentation-httpx`
   - `opentelemetry-instrumentation-requests`
2. Check that `get_tracer()` is called before creating spans
3. Verify spans are created within an active context

## OpenTelemetry Metrics

In addition to traces, the application exports OpenTelemetry metrics:

### Token Usage Counter
**Metric:** `gen_ai.client.token.usage` (Counter)

Tracks token consumption in real-time.

**Attributes:**
- `gen_ai.token.type` - "input" or "output"
- `gen_ai.request.model` - Model name
- `gen_ai.operation.name` - Operation identifier

**Query Example:**
```kusto
customMetrics
| where name == "gen_ai.client.token.usage"
| extend 
    token_type = tostring(customDimensions["gen_ai.token.type"]),
    model = tostring(customDimensions["gen_ai.request.model"])
| summarize TotalTokens = sum(value) by token_type, model
```

### Operation Duration Histogram
**Metric:** `gen_ai.client.operation.duration` (Histogram)

Tracks LLM operation latency distribution.

**Attributes:**
- `gen_ai.request.model` - Model name
- `gen_ai.operation.name` - Operation identifier

**Query Example:**
```kusto
customMetrics
| where name == "gen_ai.client.operation.duration"
| extend 
    model = tostring(customDimensions["gen_ai.request.model"]),
    operation = tostring(customDimensions["gen_ai.operation.name"])
| summarize 
    avg_duration = avg(value),
    p95_duration = percentile(value, 95)
by model, operation
```

## Prompt & Response Logging

The application supports optional prompt/response content logging via span events.

### Configuration

**Enable logging** (disabled by default for privacy):
```bash
export ENABLE_PROMPT_LOGGING=true
```

**Control truncation** (default: 1000 characters):
```bash
export PROMPT_LOG_MAX_LENGTH=2000
```

### Span Events

When enabled, each LLM call logs two events:

**Event:** `gen_ai.content.prompt`
- `gen_ai.prompt` - The prompt text (truncated)
- `gen_ai.prompt.length` - Full prompt length
- `gen_ai.prompt.truncated` - Boolean

**Event:** `gen_ai.content.completion`
- `gen_ai.completion` - The completion text (truncated)
- `gen_ai.completion.length` - Full completion length
- `gen_ai.completion.truncated` - Boolean

### Privacy Considerations

- Disabled by default
- Automatic truncation to prevent large data volumes
- Stored as events (not indexed attributes)
- Configurable per environment

## Best Practices

1. **Use correlation IDs** - The `operation_Id` links all spans in a request
2. **Follow semantic conventions** - Use OpenTelemetry Gen AI standards for LLM attributes
3. **Capture errors** - Use `span.record_exception()` and set error status
4. **Add business metrics** - Include domain-specific attributes (task type, user info)
5. **Monitor token usage** - Track costs and optimize prompts based on metrics
6. **Set up alerts** - Create Azure Monitor alerts for high latency, error rates, or token thresholds
7. **Use sampling for scale** - Configure sampling in production to manage costs
8. **Control prompt logging** - Only enable in dev/test environments due to privacy and data volume
9. **Leverage metrics** - Use counters and histograms for real-time dashboards and alerting
10. **Review periodically** - Analyze performance trends and optimize based on telemetry data

## Next Steps

- Create custom Azure Monitor workbooks for LLM analytics dashboards
- Set up alerting rules for token usage thresholds and performance degradation
- Implement trace sampling strategies for high-volume production scenarios
- Add metrics exporters for real-time performance monitoring
- Integrate distributed tracing across microservices
- Configure logging exporter for structured log analysis
- Explore OpenLLMetry integration for automatic instrumentation
- Add RAG observability with `gen_ai.retrieval.*` attributes for vector database queries

## Additional Resources

- [OpenTelemetry Gen AI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [Azure Monitor Documentation](https://docs.microsoft.com/azure/azure-monitor/)
- [OpenTelemetry Metrics API](https://opentelemetry.io/docs/specs/otel/metrics/api/)
- [KQL Language Reference](https://docs.microsoft.com/azure/data-explorer/kusto/query/)
