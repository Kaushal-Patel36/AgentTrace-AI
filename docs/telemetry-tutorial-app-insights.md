# Application Insights Telemetry Visualization Guide

This guide walks you through viewing and analyzing your LLM observability telemetry in Azure Application Insights. Each option provides different perspectives on your data, from detailed single-trace analysis to aggregated performance metrics.

## Prerequisites

1. You've deployed infrastructure via Terraform
2. You've executed at least one HTTP POST request to your service
3. Wait 1-2 minutes for telemetry to flow to Application Insights

## Opening Application Insights

**Method 1: Azure Portal**
1. Navigate to **Azure Portal** (portal.azure.com)
2. Go to **Resource Groups** → **llmobs-rg**
3. Click on **llmobs-ai** (Application Insights resource)

**Method 2: Azure CLI**
```bash
az portal open --resource-group llmobs-rg --resource-name llmobs-ai --resource-type Microsoft.Insights/components
```

---

## Option 1: Transaction Search (Best for Single Trace Analysis)

**Use this when:** You want to see the complete execution flow of a specific request with full span hierarchy.

### Steps

1. **Navigate to Transaction Search**
   - In Application Insights, click left menu: **Investigate → Transaction search**

2. **Set Time Range**
   - Top toolbar: Select **Last 30 minutes** (or adjust as needed)

3. **Filter Your Request**
   - Option A: Use search box and type part of your task (e.g., "RV road trip")
   - Option B: Filter by **Event types** → Select **Request**

4. **View the Trace**
   - Click on the **POST /run** request that matches your execution
   - The **End-to-end transaction details** pane opens

5. **Explore the Visual Timeline**
   You'll see a waterfall/timeline visualization showing:
   - **HTTP POST /run** - The root request (FastAPI)
   - **workflow.execution** - Parent workflow span
   - **planner.agent** - First agent execution
     - **llm.completion.planner** - LLM call with duration
       - **POST to Azure OpenAI** - HTTP call to API
   - **worker.agent** - Second agent execution
     - **llm.completion.worker** - LLM call
   - **reflection.agent** - Validation agent
     - **llm.completion.reflection** - LLM call
   - **reviewer.agent** - Final review agent
     - **llm.completion.reviewer** - LLM call

6. **Inspect Span Details**
   - Click any span in the timeline
   - Right panel shows **Custom Properties**:
     - `gen_ai.usage.input_tokens` - Input tokens
     - `gen_ai.usage.output_tokens` - Output tokens
     - `gen_ai.request.model` - Model used
     - `gen_ai.operation.name` - Operation identifier
     - `workflow.task` - Original user input
     - Duration and timestamps

### What You Learn
- Exact execution flow and timing
- Which agent took the longest
- Token consumption per LLM call
- Where errors occurred (if any)
- Complete context propagation via operation_Id

---

## Option 2: Application Map (System-Level Dependency View)

**Use this when:** You want to see overall service architecture, dependencies, and health metrics.

### Steps

1. **Navigate to Application Map**
   - Left menu: **Investigate → Application Map**

2. **Explore the Visualization**
   You'll see nodes and connections:
   - **Central node**: langgraph-multi-agent (your service)
   - **Connected nodes**: External dependencies (Azure OpenAI endpoints)
   - **Arrows**: Call direction and volume

3. **View Metrics on Nodes**
   Each node displays:
   - **Call count** - Number of requests
   - **Average duration** - Response time
   - **Failure rate** - Percentage of failed calls

4. **Drill Into Dependencies**
   - Click on the **Azure OpenAI** dependency node
   - See:
     - Server response times
     - Failed request details
     - Performance over time

5. **Analyze Bottlenecks**
   - Look for nodes with high duration (red/orange indicators)
   - Identify dependencies with high failure rates
   - See which external services are called most frequently

### What You Learn
- Overall system architecture
- External service dependencies
- Performance bottlenecks
- Failure patterns across services
- Call volume distribution

---

## Option 3: Performance Blade (Operation-Level Analysis)

**Use this when:** You want aggregated performance data across multiple requests with drill-down capability.

### Steps

1. **Navigate to Performance**
   - Left menu: **Investigate → Performance**

2. **Select Operation**
   - You'll see a list of operations (endpoints)
   - Click on **POST /run**

3. **View Duration Distribution**
   - See histogram showing response time distribution
   - Identify p50, p95, p99 percentiles
   - Spot outliers and slow requests

4. **Explore Dependencies**
   - Below the main chart, see **Top dependencies**
   - View which LLM calls or agents are slowest
   - See dependency call counts and durations

5. **Sample Individual Traces**
   - Right panel: **Samples** section
   - Click **"View all"** or select specific samples
   - Pick any sample to see full trace visualization (same as Transaction Search)

6. **Analyze Trends**
   - Top toolbar: Adjust time range (e.g., last 24 hours)
   - See performance trends over time
   - Identify performance degradation patterns

### What You Learn
- Average response times
- Performance distribution and outliers
- Which dependencies are slowest
- Performance trends over time
- Percentage of slow vs fast requests

---

## Option 4: Custom Workbook (Advanced Dashboards)

**Use this when:** You want persistent, customized visualizations for token usage, costs, and performance.

### Steps

1. **Create New Workbook**
   - Left menu: **Monitoring → Workbooks**
   - Click **+ New** (or start from a template)

2. **Add Query: Token Usage by Agent**
   - Click **Add → Add query**
   - Paste this KQL:
   ```kusto
   AppDependencies
   | where TimeGenerated > ago(24h)
   | where AppRoleName == "langgraph-multi-agent"
   | where Name startswith "llm.completion"
   | extend 
       Agent = tostring(split(Name, ".")[2]),
       InputTokens = toint(Properties["gen_ai.usage.input_tokens"]),
       OutputTokens = toint(Properties["gen_ai.usage.output_tokens"])
   | summarize 
       TotalInput = sum(InputTokens),
       TotalOutput = sum(OutputTokens)
   by Agent
   | order by TotalInput desc
   ```
   - Click **Run Query**
   - Change visualization: **Chart Type → Pie chart** or **Bar chart**
   - Click **Done Editing**

3. **Add Query: Performance by Agent**
   - Click **Add → Add query**
   - Paste this KQL:
   ```kusto
   AppDependencies
   | where TimeGenerated > ago(24h)
   | where AppRoleName == "langgraph-multi-agent"
   | where Name has ".agent"
   | summarize 
       AvgDuration = avg(DurationMs),
       p95Duration = percentile(DurationMs, 95),
       Count = count()
   by Name
   | order by AvgDuration desc
   ```
   - Change visualization: **Chart Type → Column chart**
   - Click **Done Editing**

4. **Add Query: Cost Estimation**
   - Click **Add → Add query**
   - Paste this KQL:
   ```kusto
   // GPT-4o-mini pricing: Input $0.150/1M, Output $0.600/1M
   AppDependencies
   | where TimeGenerated > ago(24h)
   | where Name startswith "llm.completion"
   | extend 
       InputTokens = toint(Properties["gen_ai.usage.input_tokens"]),
       OutputTokens = toint(Properties["gen_ai.usage.output_tokens"]),
       Model = tostring(Properties["gen_ai.request.model"])
   | summarize 
       TotalInputTokens = sum(InputTokens),
       TotalOutputTokens = sum(OutputTokens),
       TotalCalls = count()
   by Model
   | extend 
       InputCost = TotalInputTokens * 0.150 / 1000000.0,
       OutputCost = TotalOutputTokens * 0.600 / 1000000.0,
       TotalCost = InputCost + OutputCost
   | project Model, TotalCalls, TotalInputTokens, TotalOutputTokens, 
             InputCost = round(InputCost, 4), 
             OutputCost = round(OutputCost, 4), 
             TotalCost = round(TotalCost, 4)
   ```
   - Visualization: **Table**
   - Click **Done Editing**

5. **Add Query: Request Volume Over Time**
   - Click **Add → Add query**
   - Paste this KQL:
   ```kusto
   AppRequests
   | where TimeGenerated > ago(24h)
   | where AppRoleName == "langgraph-multi-agent"
   | summarize RequestCount = count() by bin(TimeGenerated, 1h)
   | order by TimeGenerated asc
   ```
   - Visualization: **Line chart** or **Area chart**
   - Click **Done Editing**

6. **Save the Workbook**
   - Top toolbar: Click **Save**
   - Name: "LLM Observability Dashboard"
   - Location: llmobs-rg resource group
   - Click **Save**

### What You Learn
- Token consumption trends by agent
- Cost analysis (daily/hourly spending)
- Performance comparisons across agents
- Request volume patterns
- Custom business metrics

---

## Option 5: Live Metrics (Real-Time Streaming)

**Use this when:** You want to see telemetry as it happens in real-time, perfect for debugging and demonstrations.

### Steps

1. **Navigate to Live Metrics**
   - Left menu: **Investigate → Live Metrics**

2. **Observe Real-Time Data**
   You'll see multiple panels:
   - **Incoming Requests** - Request rate per second
   - **Outgoing Requests** - Dependency call rate
   - **Overall Health** - Success/failure indicators
   - **Servers** - Number of active instances
   - **Sample Telemetry** - Live trace stream

3. **Trigger a New Request**
   Open a terminal and run:
   ```bash
   curl -X POST http://localhost:8000/run \
     -H 'Content-Type: application/json' \
     -d '{"task": "Test live metrics visualization"}'
   ```

4. **Watch Data Flow**
   Within seconds, you'll see:
   - **Request counter increments**
   - **Dependency calls appear** (4 LLM calls)
   - **Duration metrics update**
   - **Sample traces stream** in the bottom panel

5. **Explore Sample Traces**
   - Bottom panel shows live trace samples
   - See operation names as they execute:
     - POST /run
     - workflow.execution
     - llm.completion.planner
     - llm.completion.worker
     - etc.
   - Duration appears instantly

### What You Learn
- System is actively processing requests
- Real-time performance characteristics
- Immediate feedback on code changes
- Live dependency health
- Active instance count

---

## Option 6: Logs with KQL Queries

**Use this when:** You need flexible, ad-hoc analysis with custom queries and aggregations.

### Steps

1. **Navigate to Logs**
   - Left menu: **Monitoring → Logs**
   - Close the welcome dialog (if shown)

2. **Query: View Recent Requests**
   ```kusto
   AppRequests
   | where TimeGenerated > ago(1h)
   | where AppRoleName == "langgraph-multi-agent"
   | project 
       TimeGenerated, 
       Name, 
       Url, 
       DurationMs,
       Success,
       Task = tostring(Properties["workflow.task"])
   | order by TimeGenerated desc
   ```
   - Click **Run**
   - Results appear in table format
   - Click **Chart** button for visualizations

3. **Query: LLM Token Usage with Details**
   ```kusto
   AppDependencies
   | where TimeGenerated > ago(1h)
   | where Name startswith "llm.completion"
   | extend 
       Agent = tostring(split(Name, ".")[2]),
       InputTokens = toint(Properties["gen_ai.usage.input_tokens"]),
       OutputTokens = toint(Properties["gen_ai.usage.output_tokens"]),
       Model = tostring(Properties["gen_ai.request.model"])
   | project 
       TimeGenerated,
       Agent,
       DurationMs,
       InputTokens,
       OutputTokens,
       Model
   | order by TimeGenerated asc
   ```
   - Shows all LLM calls with token metrics
   - Export to CSV: Click **Export → Export to CSV**

4. **Query: Complete Workflow Trace**
   ```kusto
   let TimeRange = ago(1h);
   let OperationId = toscalar(
       AppRequests
       | where TimeGenerated > TimeRange
       | where AppRoleName == "langgraph-multi-agent"
       | top 1 by TimeGenerated desc
       | project OperationId
   );
   union AppRequests, AppDependencies
   | where OperationId == OperationId
   | extend Type = iff(itemType == "Request", "Request", "Dependency")
   | project 
       TimeGenerated,
       Type,
       Name,
       DurationMs,
       Properties
   | order by TimeGenerated asc
   ```
   - Shows complete trace for most recent request
   - All spans in chronological order

5. **Query: Performance Percentiles**
   ```kusto
   AppDependencies
   | where TimeGenerated > ago(24h)
   | where Name startswith "llm.completion"
   | extend Agent = tostring(split(Name, ".")[2])
   | summarize 
       p50 = percentile(DurationMs, 50),
       p95 = percentile(DurationMs, 95),
       p99 = percentile(DurationMs, 99),
       avg = avg(DurationMs),
       count = count()
   by Agent
   | order by p95 desc
   ```
   - Identifies slowest agents
   - Shows performance distribution

6. **Query: Error Rate Analysis**
   ```kusto
   AppDependencies
   | where TimeGenerated > ago(24h)
   | where Name startswith "llm.completion"
   | extend Agent = tostring(split(Name, ".")[2])
   | summarize 
       Total = count(),
       Successes = countif(Success == true),
       Failures = countif(Success == false),
       SuccessRate = countif(Success == true) * 100.0 / count()
   by Agent
   | order by SuccessRate asc
   ```
   - Shows which agents have errors
   - Calculate reliability metrics

7. **Save Queries**
   - Click **Save** button (top toolbar)
   - Name your query (e.g., "LLM Token Usage")
   - Access saved queries later from **Queries** tab

### What You Learn
- Custom analysis with full query flexibility
- Token usage patterns
- Cost projections
- Performance bottlenecks
- Error patterns and reliability
- Export data for external analysis

---

## Quick Reference: When to Use Each Option

| Option | Best For | Time to Result |
|--------|----------|----------------|
| **Transaction Search** | Debugging specific requests, understanding execution flow | Immediate |
| **Application Map** | System architecture, dependency health, high-level overview | Immediate |
| **Performance** | Aggregated metrics, identifying slow operations | Immediate |
| **Workbooks** | Custom dashboards, sharing reports, executive summaries | 15 minutes setup |
| **Live Metrics** | Real-time monitoring, demos, active debugging | Real-time |
| **Logs (KQL)** | Ad-hoc analysis, custom queries, data export | Immediate |

---

## Example Workflow: Analyzing a New Deployment

1. **Start with Live Metrics** - Verify telemetry is flowing
2. **Check Application Map** - Ensure all dependencies are healthy
3. **Use Transaction Search** - Find a sample request and inspect the trace
4. **Run KQL Queries** - Analyze token usage and costs
5. **Create Workbook** - Build dashboard for ongoing monitoring
6. **Set Up Alerts** - Configure alerts based on thresholds discovered

---

## Tips and Best Practices

### Transaction Search
- Use filters to narrow down searches quickly
- Bookmark frequently accessed traces
- Export trace data for offline analysis

### Application Map
- Regularly check for new unexpected dependencies
- Set up alerts on dependency failure rates
- Use this view for architecture documentation

### Performance Blade
- Compare performance before/after code changes
- Set performance baselines
- Monitor p95/p99 for SLA compliance

### Workbooks
- Start with templates and customize
- Share workbooks with team via Access Control
- Schedule automated reports (premium feature)

### Live Metrics
- Great for demos and customer presentations
- Use during deployments to verify health
- Keep open during troubleshooting sessions

### KQL Queries
- Save commonly used queries
- Use query parameterization for flexibility
- Combine with Azure Monitor Alerts for automation

---

## Troubleshooting

### No Data Appearing
1. Check `APPINSIGHTS_CONNECTION_STRING` is set correctly
2. Verify telemetry is being generated (check logs)
3. Wait 1-2 minutes for batch processing
4. Ensure firewall allows HTTPS to `*.applicationinsights.azure.com`

### Missing Request Spans
1. Verify FastAPI instrumentation is enabled
2. Check that tracer is initialized before app creation
3. Restart application after code changes

### Missing LLM Metrics
1. Confirm Azure OpenAI credentials are set
2. Verify model deployment exists
3. Check that LLM calls are succeeding (not returning errors)

### Performance Issues
1. Use sampling if volume is very high
2. Adjust batch size in instrumentation
3. Consider adaptive sampling strategies

---

## Next Steps

- Set up **Azure Monitor Alerts** based on metrics discovered
- Create **Azure Dashboards** combining multiple workbooks
- Integrate with **Azure DevOps** or **GitHub Actions** for CI/CD monitoring
- Explore **Application Insights Analytics** for advanced scenarios
- Set up **Smart Detection** for anomaly detection

---

## Additional Resources

- [Application Insights Documentation](https://docs.microsoft.com/azure/azure-monitor/app/app-insights-overview)
- [KQL Quick Reference](https://docs.microsoft.com/azure/data-explorer/kql-quick-reference)
- [OpenTelemetry Specification](https://opentelemetry.io/docs/specs/otel/)
- Project TELEMETRY.md - Detailed instrumentation reference
- Project README.md - Setup and deployment instructions
