"""Generates the importable n8n workflow JSON files in n8n/workflows/.

Why generate instead of hand-writing JSON? The four workflows share many nodes (backend calls,
notification channel switch). One script keeps them consistent and easy to review.

Run:  backend\\.venv\\Scripts\\python scripts\\build_n8n_workflows.py
Node parameter names were taken from `n8n export:nodes` of n8n 2.11.3.
"""
import json
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "n8n" / "workflows"
BACKEND = "http://127.0.0.1:8000/api/v1"          # replaced by import-n8n.ps1 if different
CRED = {"httpHeaderAuth": {"id": "WaAgentBackendK1", "name": "WA Agent Backend (internal key)"}}

IDS = {
    "incoming": "WaAgentIncoming1",
    "lead": "WaAgentLeadNoti1",
    "handoff": "WaAgentHandoff01",
    "followup": "WaAgentFollowUp1",
    "errors": "WaAgentErrors001",
    "drill": "WaAgentErrDrill1",
}
SETTINGS = {"executionOrder": "v1", "saveDataSuccessExecution": "all", "saveDataErrorExecution": "all",
            "saveManualExecutions": True, "callerPolicy": "workflowsFromSameOwner",
            "timezone": "Asia/Karachi"}


def nid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"wa-agent/{name}"))   # stable ids between builds


def node(name, type_, version, pos, params, **extra):
    n = {"parameters": params, "id": nid(name), "name": name, "type": f"n8n-nodes-base.{type_}",
         "typeVersion": version, "position": list(pos)}
    n.update(extra)
    return n


def http(name, pos, method, path, *, json_body=None, auth=True, retry=True, full=False,
         timeout=30000, extra_params=None, note=None):
    params = {"method": method, "url": f"={BACKEND}{path}" if "{{" in path else f"{BACKEND}{path}",
              "options": {"timeout": timeout}}
    if auth:
        params.update({"authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth"})
    if json_body is not None:
        params.update({"sendBody": True, "specifyBody": "json", "jsonBody": json_body})
    if full:
        params["options"]["response"] = {"response": {"fullResponse": True, "neverError": True}}
    if extra_params:
        params.update(extra_params)
    extra = {}
    if auth:
        extra["credentials"] = CRED
    if retry:
        # Safe ONLY because every backend endpoint we call is idempotent.
        extra.update({"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 2000})
    if note:
        extra.update({"notes": note, "notesInFlow": True})
    return node(name, "httpRequest", 4.2, pos, params, **extra)


def cond(left, op_type, operation, right=None):
    c = {"id": nid(f"{left}{operation}{right}"), "leftValue": left,
         "operator": {"type": op_type, "operation": operation}}
    if right is None:
        c["operator"]["singleValue"] = True
        c["rightValue"] = ""
    else:
        c["rightValue"] = right
    return c


def if_node(name, pos, conditions, combinator="and", note=None):
    params = {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose",
                                         "version": 2},
                             "conditions": conditions, "combinator": combinator},
              "options": {}}
    extra = {"notes": note, "notesInFlow": True} if note else {}
    return node(name, "if", 2.2, pos, params, **extra)


def set_node(name, pos, values: dict, keep=True):
    assigns = [{"id": nid(name + k), "name": k, "value": v,
                "type": "array" if isinstance(v, str) and v.startswith("={{") and "ids" in k else "string"}
               for k, v in values.items()]
    return node(name, "set", 3.4, pos, {"assignments": {"assignments": assigns},
                                        "includeOtherFields": keep, "options": {}})


def notify_nodes(prefix_x, y, after: str, conns: dict, related_expr: str,
                 channel_expr: str = "$('Settings').first().json.notify_channel"):
    """Channel switch: 'gmail' sends a real email, 'log' records it in the backend audit log."""
    use_gmail = if_node("Use Gmail?", (prefix_x, y),
                        [cond(f"={{{{ {channel_expr} }}}}", "string", "equals", "gmail")],
                        note="Set notify_channel = gmail in 'Settings' after connecting Gmail")
    gmail = node("Send Gmail", "gmail", 2.1, (prefix_x + 260, y - 100),
                 {"sendTo": "={{ $json.to }}", "subject": "={{ $json.subject }}", "emailType": "text",
                  "message": "={{ $json.body }}", "options": {"appendAttribution": False}},
                 notes="Needs a Gmail OAuth2 credential (see docs/N8N_SETUP.md)", notesInFlow=True)
    log = http("Log notification (backend)", (prefix_x + 260, y + 100), "POST", "/internal/notifications/log",
               json_body="={{ JSON.stringify({ channel: 'log', to: $json.to, subject: $json.subject, "
                         f"body: $json.body, related: {related_expr} }}) }}}}")
    conns[after] = {"main": [[{"node": "Use Gmail?", "type": "main", "index": 0}]]}
    conns["Use Gmail?"] = {"main": [[{"node": "Send Gmail", "type": "main", "index": 0}],
                                    [{"node": "Log notification (backend)", "type": "main", "index": 0}]]}
    return [use_gmail, gmail, log]


def link(conns, src, *targets, output=0):
    outs = conns.setdefault(src, {"main": []})["main"]
    while len(outs) <= output:
        outs.append([])
    outs[output].extend({"node": t, "type": "main", "index": 0} for t in targets)


def workflow(key, name, nodes, conns, tags):
    settings = dict(SETTINGS)
    if key != "errors":
        # When a production run of this workflow fails, n8n starts WA 5 (Error Trigger).
        settings["errorWorkflow"] = IDS["errors"]
    return {"id": IDS[key], "name": name, "nodes": nodes, "connections": conns, "active": False,
            "settings": settings, "pinData": {}, "meta": {"templateCredsSetupCompleted": True},
            "tags": []}   # tags left out: n8n import creates duplicates


def exec_sub(name, pos, key, note):
    return node(name, "executeWorkflow", 1.2, pos, {
        "source": "database",
        "workflowId": {"__rl": True, "value": IDS[key], "mode": "id"},
        "workflowInputs": {"mappingMode": "defineBelow", "value": {}, "matchingColumns": [], "schema": [],
                           "attemptToConvertTypes": False, "convertFieldsToString": True},
        "mode": "each",
        "options": {"waitForSubWorkflow": False},
    }, notes=note, notesInFlow=True)


# ============================================================ Workflow 1
def wf_incoming():
    c: dict = {}
    nodes = [
        node("Meta verification (GET)", "webhook", 2.1, (0, 0),
             {"httpMethod": "GET", "path": "whatsapp", "responseMode": "responseNode", "options": {}},
             webhookId=nid("webhook-get")),
        http("Verify token (backend)", (240, 0), "GET", "/webhooks/whatsapp", auth=False, retry=False, full=True,
             extra_params={"sendQuery": True, "queryParameters": {"parameters": [
                 {"name": "hub.mode", "value": "={{ $json.query['hub.mode'] }}"},
                 {"name": "hub.verify_token", "value": "={{ $json.query['hub.verify_token'] }}"},
                 {"name": "hub.challenge", "value": "={{ $json.query['hub.challenge'] }}"}]}},
             note="Backend checks the verify token"),
        node("Respond challenge", "respondToWebhook", 1.1, (480, 0),
             {"respondWith": "text",
              # n8n puts a text response in "data" and a JSON response in "body"
              "responseBody": "={{ $json.data ?? JSON.stringify($json.body) }}",
              "options": {"responseCode": "={{ $json.statusCode }}"}}),

        node("WhatsApp message (POST)", "webhook", 2.1, (0, 300),
             {"httpMethod": "POST", "path": "whatsapp", "responseMode": "responseNode",
              "options": {"rawBody": True}},
             webhookId=nid("webhook-post"), notes="rawBody keeps Meta's exact bytes for the signature check",
             notesInFlow=True),
        http("Validate, identify & save (backend)", (240, 300), "POST", "/webhooks/whatsapp", auth=False,
             retry=False, full=True,
             extra_params={"sendBody": True, "contentType": "binaryData", "inputDataFieldName": "data",
                           "sendHeaders": True, "headerParameters": {"parameters": [
                               {"name": "X-Hub-Signature-256",
                                "value": "={{ $json.headers['x-hub-signature-256'] || '' }}"},
                               {"name": "Content-Type", "value": "application/json"}]}},
             note="Signature check, payload validation, dedupe, customer + message saved"),
        node("Respond to Meta", "respondToWebhook", 1.1, (480, 300),
             {"respondWith": "json", "responseBody": "={{ $json.body }}",
              "options": {"responseCode": "={{ $json.statusCode }}"}},
             notes="Meta gets its answer fast; AI work continues after", notesInFlow=True),
        if_node("New messages?", (720, 300), [
            cond("={{ $json.statusCode }}", "number", "equals", 200),
            cond("={{ ($json.body.new_message_ids || []).length }}", "number", "gt", 0)]),
        set_node("Message ids", (960, 280), {"message_ids": "={{ $json.body.new_message_ids }}"}, keep=False),
        node("One item per message", "splitOut", 1, (1180, 280), {"fieldToSplitOut": "message_ids", "options": {}}),
        http("AI agent (backend)", (1400, 280), "POST", "/agent/process", timeout=90000,
             json_body="={{ JSON.stringify({ message_id: $json.message_ids, send: false }) }}",
             note="Support/Sales/Qualification modules. Idempotent: never answers twice"),
        if_node("Reply to send?", (1640, 120), [cond("={{ $json.reply_message_id }}", "string", "notEmpty"),
                                               cond("={{ $json.reply_send_status }}", "string", "equals", "pending")]),
        http("Send WhatsApp reply (backend)", (1880, 100), "POST",
             "/internal/messages/{{ $json.reply_message_id }}/send",
             note="WhatsApp Cloud API via backend: safe retries, mock mode"),
        if_node("Hot lead? (new)", (1640, 320), [cond("={{ $json.notify_sales }}", "boolean", "true"),
                                                cond("={{ $json.already_processed }}", "boolean", "false")]),
        exec_sub("Notify sales (Workflow 2)", (1880, 320), "lead", "Runs WA 2 - Hot Lead Notification"),
        if_node("Needs a human?", (1640, 520), [
            cond("={{ $json.action === 'handoff' || $json.skip_reason === 'human_mode' }}", "boolean", "true"),
            cond("={{ $json.already_processed }}", "boolean", "false")]),
        exec_sub("Human handoff (Workflow 3)", (1880, 520), "handoff", "Runs WA 3 - Human Handoff"),
    ]
    link(c, "Meta verification (GET)", "Verify token (backend)")
    link(c, "Verify token (backend)", "Respond challenge")
    link(c, "WhatsApp message (POST)", "Validate, identify & save (backend)")
    link(c, "Validate, identify & save (backend)", "Respond to Meta")
    link(c, "Respond to Meta", "New messages?")
    link(c, "New messages?", "Message ids")
    link(c, "Message ids", "One item per message")
    link(c, "One item per message", "AI agent (backend)")
    link(c, "AI agent (backend)", "Reply to send?", "Hot lead? (new)", "Needs a human?")
    link(c, "Reply to send?", "Send WhatsApp reply (backend)")
    link(c, "Hot lead? (new)", "Notify sales (Workflow 2)")
    link(c, "Needs a human?", "Human handoff (Workflow 3)")
    return workflow("incoming", "WA 1 - Incoming WhatsApp Message", nodes, c, ["whatsapp-agent"])


# ============================================================ Workflow 2
def wf_lead():
    c: dict = {}
    nodes = [
        node("From Workflow 1", "executeWorkflowTrigger", 1.1, (0, 0), {"inputSource": "passthrough"}),
        set_node("Settings", (220, 0), {"notify_channel": "log"}),
        http("Lead summary (backend)", (440, 0), "GET", "/internal/leads/{{ $json.lead.id }}/notification"),
        if_node("Still hot & not notified?", (660, 0), [
            cond("={{ $json.already_notified }}", "boolean", "false"),
            cond("={{ $json.temperature }}", "string", "equals", "hot")],
            note="Never emails the same lead twice"),
    ]
    link(c, "From Workflow 1", "Settings")
    link(c, "Settings", "Lead summary (backend)")
    link(c, "Lead summary (backend)", "Still hot & not notified?")
    nodes += notify_nodes(900, 0, "Still hot & not notified?", c, "{ lead_id: $json.lead_id }")
    nodes.append(http("Mark lead notified (backend)", (1400, 0), "POST",
                      "/internal/leads/{{ $('Lead summary (backend)').first().json.lead_id }}/notified",
                      json_body="={{ JSON.stringify({ channel: $('Settings').first().json.notify_channel, "
                                "to: $('Lead summary (backend)').first().json.to }) }}"))
    link(c, "Send Gmail", "Mark lead notified (backend)")
    link(c, "Log notification (backend)", "Mark lead notified (backend)")
    return workflow("lead", "WA 2 - Hot Lead Notification", nodes, c, ["whatsapp-agent"])


# ============================================================ Workflow 3
def wf_handoff():
    c: dict = {}
    nodes = [
        node("From Workflow 1", "executeWorkflowTrigger", 1.1, (0, 0), {"inputSource": "passthrough"}),
        set_node("Settings", (220, 0), {"notify_channel": "log"}),
        if_node("New handoff?", (440, 0), [cond("={{ $json.action }}", "string", "equals", "handoff"),
                                           cond("={{ $json.handoff_request_id }}", "string", "notEmpty")],
                note="true: new handoff | false: customer wrote while a human handles the chat"),
        http("Assign agent (backend)", (680, -100), "POST",
             "/internal/handoffs/{{ $json.handoff_request_id }}/assign",
             note="AI already paused. Least-busy sales agent"),
        if_node("Customer wrote during handoff?", (680, 100), [
            cond("={{ $json.skip_reason }}", "string", "equals", "human_mode"),
            cond("={{ $json.conversation_id }}", "string", "notEmpty")],
            note="Guard: anything else (empty/unknown input) ends quietly"),
        http("Get assigned agent (backend)", (900, 160), "GET",
             "/internal/conversations/{{ $json.conversation_id }}/assignee"),
        node("Every 30 minutes", "scheduleTrigger", 1.2, (0, 400),
             {"rule": {"interval": [{"field": "minutes", "minutesInterval": 30}]}}),
        node("Run now (manual test)", "manualTrigger", 1, (0, 600), {}),
        http("Resume AI when safe (backend)", (260, 500), "POST", "/internal/handoffs/auto-resume",
             note="Only idle conversations with no human reply return to the AI"),
    ]
    link(c, "From Workflow 1", "Settings")
    link(c, "Settings", "New handoff?")
    link(c, "New handoff?", "Assign agent (backend)", output=0)
    link(c, "New handoff?", "Customer wrote during handoff?", output=1)
    link(c, "Customer wrote during handoff?", "Get assigned agent (backend)")
    nodes += notify_nodes(940, 0, "Assign agent (backend)", c, "{ handoff_id: $json.handoff_id }")
    link(c, "Get assigned agent (backend)", "Use Gmail?")
    link(c, "Every 30 minutes", "Resume AI when safe (backend)")
    link(c, "Run now (manual test)", "Resume AI when safe (backend)")
    return workflow("handoff", "WA 3 - Human Handoff", nodes, c, ["whatsapp-agent"])


# ============================================================ Workflow 4
def wf_followup():
    c: dict = {}
    nodes = [
        node("Every hour", "scheduleTrigger", 1.2, (0, 0), {"rule": {"interval": [{"field": "hours"}]}}),
        node("Run now (manual test)", "manualTrigger", 1, (0, 200), {}),
        http("Plan follow-ups (backend)", (260, 100), "POST", "/internal/follow-ups/plan",
             note="Eligible leads + consent + 24h/template rules"),
        http("Get approved & due (backend)", (500, 100), "GET", "/internal/follow-ups/due",
             note="Only human-approved tasks"),
        set_node("Task ids", (740, 100), {"task_ids": "={{ $json.task_ids }}"}, keep=False),
        node("One item per task", "splitOut", 1, (960, 100), {"fieldToSplitOut": "task_ids", "options": {}}),
        http("Send follow-up (backend)", (1180, 100), "POST", "/internal/follow-ups/{{ $json.task_ids }}/send",
             note="Re-checks consent & rules, sends template, records result"),
    ]
    link(c, "Every hour", "Plan follow-ups (backend)")
    link(c, "Run now (manual test)", "Plan follow-ups (backend)")
    link(c, "Plan follow-ups (backend)", "Get approved & due (backend)")
    link(c, "Get approved & due (backend)", "Task ids")
    link(c, "Task ids", "One item per task")
    link(c, "One item per task", "Send follow-up (backend)")
    return workflow("followup", "WA 4 - Follow-up Automation", nodes, c, ["whatsapp-agent"])


# ============================================================ Workflow 5
def wf_errors():
    """Error Trigger + scheduled recovery. The backend decides; n8n only reports, retries & alerts."""
    c: dict = {}
    alert_fields = {"error_id": "={{ $json.error_id }}", "to": "={{ $json.to }}",
                    "subject": "={{ $json.subject }}", "body": "={{ $json.body }}",
                    "notify_channel": "log"}       # change to "gmail" after connecting Gmail
    nodes = [
        node("Error Trigger", "errorTrigger", 1, (0, 0), {}),
        http("Report error (backend)", (240, 0), "POST", "/internal/automation-errors",
             json_body="={{ JSON.stringify({ workflow_id: $json.workflow.id, workflow_name: $json.workflow.name, "
                       "execution_id: $json.execution.id, node_name: $json.execution.lastNodeExecuted, "
                       "error_message: ($json.execution.error && $json.execution.error.message) || 'Unknown error', "
                       "http_code: parseInt(($json.execution.error && $json.execution.error.httpCode) || '') || null, "
                       "mode: $json.execution.mode }) }}",
             note="Backend classifies: retry (safe) / alert (needs fix) / approval (risky)"),
        if_node("Alert now?", (480, 0), [cond("={{ $json.alert }}", "boolean", "true")],
                note="Grouped repeats are not alerted twice"),
        set_node("Alert (from trigger)", (700, -60), alert_fields, keep=False),

        node("Every 5 minutes", "scheduleTrigger", 1.2, (0, 300),
             {"rule": {"interval": [{"field": "minutes", "minutesInterval": 5}]}}),
        node("Run now (manual test)", "manualTrigger", 1, (0, 480), {}),
        http("Run safe retries (backend)", (240, 380), "POST", "/internal/recovery/run",
             note="Only SAFE errors whose retry time has come"),
        http("Pending alerts (backend)", (480, 380), "GET", "/internal/recovery/alerts",
             note="e.g. retries exhausted, failures recorded by the backend itself"),
        set_node("Alert list", (700, 380), {"alert_ids": "={{ $json.alerts }}"}, keep=False),
        node("One item per alert", "splitOut", 1, (900, 380), {"fieldToSplitOut": "alert_ids", "options": {}}),
        # Split Out may or may not wrap objects under the field name: accept both shapes.
        set_node("Alert (from schedule)", (1100, 380),
                 {k: v.replace("$json.", "($json.alert_ids || $json).") for k, v in alert_fields.items()},
                 keep=False),
    ]
    # Manual trigger first: `n8n execute` starts from the first trigger in the list.
    nodes.insert(0, nodes.pop(next(i for i, n in enumerate(nodes) if n["name"] == "Run now (manual test)")))
    link(c, "Error Trigger", "Report error (backend)")
    link(c, "Report error (backend)", "Alert now?")
    link(c, "Alert now?", "Alert (from trigger)")
    link(c, "Every 5 minutes", "Run safe retries (backend)")
    link(c, "Run now (manual test)", "Run safe retries (backend)")
    link(c, "Run safe retries (backend)", "Pending alerts (backend)")
    link(c, "Pending alerts (backend)", "Alert list")
    link(c, "Alert list", "One item per alert")
    link(c, "One item per alert", "Alert (from schedule)")
    nodes += notify_nodes(1340, 100, "Alert (from trigger)", c, "{ error_id: $json.error_id }",
                          channel_expr="$json.notify_channel")
    link(c, "Alert (from schedule)", "Use Gmail?")
    nodes.append(http("Mark alerted (backend)", (1840, 100), "POST",
                      "/internal/automation-errors/{{ $('Use Gmail?').item.json.error_id }}/alerted",
                      note="So the same error group is never announced twice"))
    link(c, "Send Gmail", "Mark alerted (backend)")
    link(c, "Log notification (backend)", "Mark alerted (backend)")
    return workflow("errors", "WA 5 - Error Handler & Recovery", nodes, c, [])


# ============================================================ Workflow 9 (demo)
def wf_drill():
    """Fails on purpose (calls a closed port) so the error chain can be shown live."""
    c: dict = {}
    nodes = [
        node("Start drill (POST)", "webhook", 2.1, (0, 0),
             {"httpMethod": "POST", "path": "error-drill", "responseMode": "onReceived", "options": {}},
             webhookId=nid("webhook-drill")),
        node("Call a service that is down", "httpRequest", 4.2, (260, 0),
             {"method": "GET", "url": "http://127.0.0.1:9/unreachable", "options": {"timeout": 3000}},
             notes="Port 9 is closed -> ECONNREFUSED -> WA 5 takes over", notesInFlow=True),
    ]
    link(c, "Start drill (POST)", "Call a service that is down")
    return workflow("drill", "WA 9 - Error Drill (demo)", nodes, c, [])


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for filename, wf in [("01_incoming_message.json", wf_incoming()), ("02_hot_lead_notification.json", wf_lead()),
                         ("03_human_handoff.json", wf_handoff()), ("04_follow_up_automation.json", wf_followup()),
                         ("05_error_handler.json", wf_errors()), ("09_error_drill_demo.json", wf_drill())]:
        (OUT / filename).write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote n8n/workflows/{filename}: {len(wf['nodes'])} nodes")
