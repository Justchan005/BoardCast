"""BoardCast agents (ADK multi-agent).

    root_agent (coordinator)
      ├── intake_agent  -> club reps: read poster, confirm, submit, fix
      └── review_agent  -> Student Affairs: list, approve/reject, hold issue

The coordinator only routes. Each specialist has its own prompt file and only the tools
it needs, so a club rep conversation can never reach the approval tools.
Deploy to Agent Engine, register in Gemini Enterprise, share with Club reps + Student Affairs only.
"""
import os

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.plugins.save_files_as_artifacts_plugin import SaveFilesAsArtifactsPlugin

from .prompt_loader import load
from .tools import decide_item, fix_item, hold_issue, list_pending, read_attached_poster, submit_item

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

intake = load("intake")
intake_agent = Agent(
    name="intake_agent",
    model=MODEL,
    description="Helps club reps and APU offices submit or fix an event, announcement or achievement.",
    instruction=intake.text,
    tools=[read_attached_poster, submit_item, fix_item, list_pending],
)

review = load("review")
review_agent = Agent(
    name="review_agent",
    model=MODEL,
    description="Helps Student Affairs review, approve or reject submissions and hold the Monday newsletter.",
    instruction=review.text,
    tools=[list_pending, decide_item, hold_issue],
)

coordinator = load("coordinator")
root_agent = Agent(
    name="boardcast_agent",
    model=MODEL,
    description="BoardCast: APU campus newsletter assistant for club reps and Student Affairs.",
    instruction=coordinator.text,
    sub_agents=[intake_agent, review_agent],
)

PROMPT_VERSIONS = {p.name: p.version for p in (intake, review, coordinator)}

# Files attached in chat arrive as inline data; this plugin saves them as session artifacts
# so read_attached_poster() can load them (ADK loads `app` before `root_agent`).
app = App(name="boardcast_agent", root_agent=root_agent, plugins=[SaveFilesAsArtifactsPlugin()])
