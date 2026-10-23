"""JARVIS main handler: intent routing, specialist delegation and conversation context."""
from __future__ import annotations

from collections import deque
import inspect
import re
from typing import Any
from types import SimpleNamespace
from urllib.parse import urlparse

from core.contracts import TaskRequest
from core.router import CapabilityRouter
from .model_client import ModelClientError, OpenAICompatibleClient


_WAKE_WORD_RE = re.compile(r"\bjarvis\b", re.IGNORECASE)


class VoiceAgent:
    """Primary coordinator for all user chat and future speech transcripts.

    This agent does not impersonate unimplemented specialists. It routes to
    executable handlers, preserves a bounded in-memory history, and can fan out
    obvious compound requests when the matching specialist handlers are ready.
    """

    def __init__(self, history_limit: int = 12, model_client: Any | None = None) -> None:
        self._history: deque[dict[str, str]] = deque(maxlen=max(2, history_limit))
        self.model_client = model_client or OpenAICompatibleClient()

    async def __call__(self, task: TaskRequest, context: Any) -> Any:
        prompt = task.prompt.strip()
        if not prompt:
            return "I didn't receive a request. Please say or type what you'd like me to do."

        if self._is_exit_command(prompt):
            return "I understand. You can exit the voice session from the terminal with Ctrl+C."

        if self._is_greeting(prompt):
            reply = (
                "JARVIS online. The agent network is connected. "
                f"{len(context.runtime.registry.list(enabled_only=True))} agent identities are registered; "
                f"{len(context.runtime.executable_agent_ids())} currently have executable handlers."
            )
            self._remember(prompt, reply)
            return reply

        # Keep short conversational follow-ups in the conversation layer. These
        # should not be mistaken for specialist tasks just because they are not
        # standalone questions (e.g. replying "I'm good" after JARVIS asks).
        contextual_reply = self._contextual_conversation_reply(prompt)
        if contextual_reply is not None:
            self._remember(prompt, contextual_reply)
            return contextual_reply

        # Ordinary social conversation should not be treated as a specialist
        # task or require a configured LLM just to answer common pleasantries.
        quick_reply = self._quick_conversation_reply(prompt)
        if quick_reply is not None:
            self._remember(prompt, quick_reply)
            return quick_reply

        router = CapabilityRouter(context.runtime.registry)
        matches = router.route(
            prompt,
            limit=8,
            minimum_score=1.0,
            exclude=(context.agent.agent_id,),
            executable_ids=context.runtime.executable_agent_ids(),
        )
        executable = [match for match in matches if match.executable]

        # Explicit research -> planning -> report requests are dependency-driven.
        # This check precedes the broader research-to-code detector so a request
        # to "create a plan" is not misclassified as a request to create code.
        if self._is_research_plan_report_request(prompt):
            response = await self._run_research_plan_report(prompt, context)
            self._remember(prompt, response)
            return response

        # Explicit research -> implementation workflows must be sequential:
        # research first, then pass the actual research payload to Forge.
        # Do not send the entire compound instruction to Scout as a search query.
        if self._is_research_to_forge_request(prompt):
            response = await self._run_research_to_forge(prompt, context)
            self._remember(prompt, response)
            return response

        # When multiple specialists are relevant, fan out clear compound requests.
        # Run only executable handlers and explicitly report requested workstreams
        # whose specialist is not active yet.
        # A search request followed by "and explain/summarize ..." is one
        # search-and-synthesis workflow, not a multi-agent fan-out. Scout must
        # receive only the search query; this handler synthesizes its results.
        search_intent = bool(re.search(
            r"\b(?:search|web search|look up|find online|google)\b", prompt, re.IGNORECASE
        ))
        synthesis_followup = bool(re.search(
            r"\s+(?:and then|then|and)\s+(?:please\s+)?"
            r"(?:explain|summari[sz]e|tell me|describe|compare|analy[sz]e|break down|give me an overview of)\b",
            prompt,
            re.IGNORECASE,
        ))
        scout_is_executable = any(match.agent_id == "search.scout" for match in executable)
        # Only suppress fan-out when the second clause asks JARVIS to synthesize
        # Scout's results. Genuine search + code/repository work still fans out.
        compound = self._is_compound_request(prompt) and not (
            search_intent and synthesis_followup and scout_is_executable
        )
        if compound:
            planned_tasks = self._build_compound_tasks(prompt, matches)
            executable_ids = context.runtime.executable_agent_ids()
            active_tasks = [item for item in planned_tasks if item["agent_id"] in executable_ids]
            unavailable = [item for item in planned_tasks if item["agent_id"] not in executable_ids]
            if len(planned_tasks) >= 2:
                results = await context.delegate_many(active_tasks) if active_tasks else []
                if results:
                    report = self._combine_results(results, matches)
                else:
                    report = "I split your request into specialist workstreams, but none of the required specialist handlers is active yet."
                if unavailable:
                    by_id = {match.agent_id: match for match in matches}
                    report += "\n\nNot run because the required specialist handler is not active yet:"
                    for item in unavailable:
                        match = by_id.get(item["agent_id"])
                        report += f"\n- {match.name if match else item['agent_id']} ({item['agent_id']})"
                self._remember(prompt, report)
                return report

        if executable:
            target = executable[0]
            child_prompt = self._prepare_search_prompt(prompt) if target.agent_id == "search.scout" else prompt
            child_context = {
                "routed_by": context.agent.agent_id,
                "matched_terms": list(target.matched_terms),
                "conversation_history": list(self._history),
            }
            # For analysis of the agent network itself, Athena can use the
            # authoritative local manifests rather than requiring Scout/network.
            # This is context injection only: it does not change agent settings.
            if target.agent_id == "research.athena" and self._is_local_agent_network_analysis(prompt):
                child_context["source_material"] = self._local_agent_network_facts(context.runtime)
                child_context["analysis_scope"] = "local_agent_manifests_and_runtime_handler_availability"
            child = await context.delegate(
                child_prompt,
                agent_id=target.agent_id,
                context=child_context,
                requested_by=context.agent.agent_id,
                capabilities=target.capabilities,
                allow_fallback=True,
            )
            if child.succeeded:
                output = child.output
                if target.agent_id == "search.scout" and isinstance(output, dict):
                    response = await self._synthesize_search_response(prompt, output)
                else:
                    response = output if isinstance(output, str) else self._render_output(output)
                if target.agent_id in {"documentation.scribe", "planning.planner", "research.athena", "memory.notes"}:
                    response = f"Specialist executed: {target.name} ({target.agent_id})\n\n{response}"
                self._remember(prompt, response)
                return response
            response = (
                f"{target.name} couldn't complete the task. Status: {child.status.value}. "
                f"Details: {child.error or child.summary}"
            )
            self._remember(prompt, response)
            return response

        if matches:
            best = matches[0]
            response = (
                f"I understand the request. It best matches {best.name} ({best.agent_id}), "
                "but that specialist's execution handler is not yet implemented or active. "
                f"Required capabilities: {', '.join(best.capabilities)}. "
                "I have not claimed the task is complete."
            )
        else:
            response = await self._model_fallback(prompt, context)
        self._remember(prompt, response)
        return response

    async def _model_fallback(self, prompt: str, context: Any) -> str:
        """Use an optional configured model for general conversation or missed routing.

        The model may propose only known registry IDs; all agent execution still
        goes through runtime checks. With no endpoint configured, remain honest
        and fall back to a concise explanation rather than hallucinating actions.
        """
        if not getattr(self.model_client, "configured", False):
            return (
                "I couldn't match this request to an active specialist. For open-ended "
                "conversation and more flexible task planning, configure a chat model in the "
                "project .env file. No action has been taken."
            )
        agents = []
        for spec in context.runtime.registry.list(enabled_only=True):
            agents.append({
                "agent_id": spec.agent_id,
                "name": spec.name,
                "role": spec.role,
                "description": spec.description,
                "capabilities": list(spec.capabilities),
                "status": context.runtime.agent_status(spec.agent_id).value,
                "executable": context.runtime.has_handler(spec.agent_id),
            })
        # General conversation should be ordinary prose, not a JSON-only agent
        # planning decision. Deterministic capability routing and explicit
        # compound-task handling have already run above.
        answer_chat = getattr(self.model_client, "answer_chat", None)
        if callable(answer_chat):
            messages = [
                {"role": "system", "content": "You are JARVIS, a concise and honest assistant. Never claim an action was taken unless it was executed."},
                *list(self._history),
                {"role": "user", "content": prompt},
            ]
            try:
                try:
                    parameters = inspect.signature(answer_chat).parameters
                except (TypeError, ValueError):
                    parameters = {}
                if "prompt" in parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
                    result = answer_chat(prompt=prompt, history=list(self._history))
                else:
                    result = answer_chat(messages)
                if inspect.isawaitable(result):
                    result = await result
                response = str(result or "").strip()
                return response[:12000] if response else "I couldn't produce a useful answer. Could you rephrase that?"
            except ModelClientError as exc:
                raise RuntimeError(f"The conversational model failed safely: {exc}") from exc
            except Exception as exc:
                raise RuntimeError(f"The conversational model failed safely ({type(exc).__name__}: {exc}).") from exc

        # Backwards-compatible adapter path for injected/custom clients that
        # implement the older decision interface but not answer_chat().
        try:
            decision = await self.model_client.decide(
                prompt=prompt,
                history=list(self._history),
                agents=agents,
            )
        except ModelClientError as exc:
            return f"My configured language model is unavailable: {exc}. No action has been taken."
        except Exception as exc:
            return f"The language-model fallback failed safely ({type(exc).__name__}: {exc}). No action has been taken."

        action = str(decision.get("action", "")).casefold().strip()
        if action in {"chat", "clarify"}:
            response = str(decision.get("response", "")).strip()
            return response[:12000] if response else "I need a little more detail to answer that reliably."
        return "I couldn't create a valid task plan. Please rephrase the request; nothing was executed."

    async def _synthesize_search_response(self, prompt: str, output: dict[str, Any]) -> str:
        """Turn Scout's raw links/snippets into a useful answer with citations."""
        query = str(output.get("query", "")).strip() or prompt
        # Scout receives a shortened query; synthesis must answer the original
        # request, including instructions such as "explain the biggest changes".
        user_question = str(prompt or query).strip().strip(' \t\r\n\"\'“”‘’') or query
        sources = output.get("results", [])
        if not isinstance(sources, list) or not sources:
            return str(output.get("summary", "Scout did not return usable search results."))

        if getattr(self.model_client, "configured", False) and callable(
            getattr(self.model_client, "answer_with_sources", None)
        ):
            try:
                # Avoid unrelated prior turns contaminating a fresh search.
                # Keep history only for genuine contextual follow-up questions.
                followup = bool(re.search(
                    r"\b(?:those|these|that|them|it|they|their|which of|why is that|what about that)\b",
                    user_question,
                    re.IGNORECASE,
                )) and not bool(re.search(
                    r"\b(?:search|look up|find online|google)\b", user_question, re.IGNORECASE
                ))
                source_payload = [item for item in sources if isinstance(item, dict)]
                # Label every snippet explicitly so downstream model prompts and
                # audit logs cannot mistake search-result excerpts for verified page content.
                formatted_sources = []
                for index, item in enumerate(source_payload, start=1):
                    title = str(item.get("title", "Untitled source")).strip() or "Untitled source"
                    url = str(item.get("url", "")).strip()
                    snippet = str(item.get("snippet", "")).strip()
                    formatted_sources.append(
                        f"[{index}] {title}\nURL: {url}\nSnippet (unverified): {snippet}"
                    )
                source_text = "\n\n".join(formatted_sources)
                messages = [
                    {"role": "system", "content": "Answer using the supplied search results. Do not invent facts or claim snippets were independently verified."},
                ]
                if followup:
                    messages.extend(list(self._history))
                messages.append({"role": "user", "content": f"Question: {user_question}\n\nSearch results (unverified):\n{source_text}"})
                answer_method = self.model_client.answer_with_sources
                try:
                    parameters = inspect.signature(answer_method).parameters
                except (TypeError, ValueError):
                    parameters = {}
                if "prompt" in parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
                    answer = answer_method(prompt=user_question, history=list(self._history) if followup else [], sources=source_payload)
                else:
                    answer = answer_method(messages)
                if inspect.isawaitable(answer):
                    answer = await answer
                answer_text = str(answer.get("answer", "") or "").strip() if isinstance(answer, dict) else str(answer or "").strip()
                if answer_text:
                    return self._format_sourced_answer(answer_text, sources, output.get("retrieved_at"))
            except ModelClientError as exc:
                model_note = f"The chat model was unavailable ({exc}); showing a source-based summary instead."
            except Exception as exc:
                model_note = f"Answer synthesis failed safely ({type(exc).__name__}); showing a source-based summary instead."
        else:
            model_note = (
                "For a fully conversational, synthesized answer, configure the optional chat model "
                "in .env; this is an extractive summary of Scout's retrieved snippets."
            )

        snippets = []
        for item in sources[:3]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "Untitled source")).strip()
            snippet = " ".join(str(item.get("snippet", "")).split())
            if snippet:
                snippets.append(f"- {title}: {snippet}")
            else:
                snippets.append(f"- {title}: Scout found this page, but its search result did not include a descriptive snippet.")
        lines = [f"JARVIS answer: I searched for {query!r}. Here is what the retrieved results say:"]
        lines.extend(snippets)
        lines.extend(["", model_note, "", "Sources:"])
        lines.extend(self._source_lines(sources))
        retrieved_at = str(output.get("retrieved_at", "")).strip()
        if retrieved_at:
            lines.extend(["", f"Retrieved: {retrieved_at}"])
        return "\n".join(lines)

    @staticmethod
    def _source_lines(sources: list[Any]) -> list[str]:
        lines = []
        for index, item in enumerate(sources[:10], start=1):
            if not isinstance(item, dict):
                continue
            title = " ".join(str(item.get("title", "Untitled source")).split())
            url = str(item.get("url", "")).strip()
            if url.startswith(("https://", "http://")):
                lines.append(f"[{index}] {title} — {url}")
        return lines

    @classmethod
    def _format_sourced_answer(cls, answer: str, sources: list[Any], retrieved_at: Any) -> str:
        lines = [f"JARVIS answer: {answer.strip()}", "", "Sources:"]
        lines.extend(cls._source_lines(sources))
        stamp = str(retrieved_at or "").strip()
        if stamp:
            lines.extend(["", f"Retrieved: {stamp}"])
        lines.extend(["", "Answers are based on search-result metadata/snippets; open the cited pages to verify details."])
        return "\n".join(lines)

    def _remember(self, prompt: str, response: str) -> None:
        self._history.append({"role": "user", "content": prompt})
        self._history.append({"role": "assistant", "content": response})

    @staticmethod
    def _is_greeting(prompt: str) -> bool:
        normalized = prompt.casefold().strip(" !.,?\t\n")
        return normalized in {
            "hi", "hello", "hey", "hey jarvis", "hello jarvis",
            "status", "are you there", "jarvis", "hey there",
        }

    def _contextual_conversation_reply(self, prompt: str) -> str | None:
        """Answer simple follow-ups using the preceding assistant turn.

        This intentionally handles only low-risk conversational patterns. It
        is not a replacement for a language model for open-ended conversation.
        """
        text = str(prompt or "").casefold().strip()
        text = text.replace("’", "'")
        text = re.sub(r"^\s*(?:(?:hey|hello)\s+)?jarvis[\s,.:;!?-]*", "", text)
        text = re.sub(r"[\s!?.,\t\n]+", " ", text).strip()
        text = re.sub(r"\s+(?:jarvis|sir)$", "", text).strip()
        text = re.sub(r"\s+(?:myself|as well|too)$", "", text).strip()
        if not self._history:
            return None

        last_assistant = next(
            (item.get("content", "") for item in reversed(self._history)
             if item.get("role") == "assistant"),
            "",
        ).casefold()

        wellbeing_responses = {
            "i am good", "i'm good", "im good", "i am fine", "i'm fine", "im fine",
            "i am great", "i'm great", "im great", "doing good", "doing well",
            "pretty good", "good", "great", "fine", "not bad", "i am okay",
            "i'm okay", "im okay", "i am ok", "i'm ok", "im ok", "all good",
            "i am doing well", "i'm doing well", "i am doing great", "i'm doing great",
            "i am doing good", "i'm doing good", "im doing good", "i feel good",
        }
        if text in wellbeing_responses and any(
            cue in last_assistant for cue in ("how are you?", "how about you?", "how are you doing?")
        ):
            return "Glad to hear that! I'm here and ready to help. What would you like to work on today?"

        if text in {"what about you", "and you", "how about you", "you", "what about yourself"}:
            if any(cue in last_assistant for cue in ("how are you?", "how about you?", "how are you doing?")):
                return "I'm doing well and ready to help. Thanks for asking! What shall we tackle?"

        if text in {"nothing", "nothing much", "not much", "just chilling", "just relaxing"}:
            if any(cue in last_assistant for cue in ("what's on your mind?", "what shall we tackle", "what would you like to work on")):
                return "Fair enough — I'm here whenever you want to build, learn, research, or just chat."

        if text in {"yes", "yeah", "yep", "sure", "okay", "ok"} and any(
            cue in last_assistant for cue in ("would you like me to", "shall i", "do you want me to")
        ):
            return "Sure — tell me what you'd like me to start with, and I'll route it to the right agent."

        return None

    @staticmethod
    def _quick_conversation_reply(prompt: str) -> str | None:
        """Handle predictable conversational turns locally and immediately.

        These responses are intentionally deterministic. More open-ended chat
        still uses the configured model adapter, but common small talk should
        never fall through to specialist routing or demand an API key.
        """
        text = str(prompt or "").casefold().strip()
        text = re.sub(r"^\s*(?:hey\s+)?jarvis[\s,.:;!?-]*", "", text)
        text = re.sub(r"[\s!?.,\t\n]+", " ", text).strip()
        text = re.sub(r"\s+sir$", "", text).strip()
        text = text.replace("’", "'")

        how_are_you = {
            "how are you", "how are u", "how r you", "how r u", "how ru",
            "how are you doing", "how're you", "how have you been",
            "how are you today", "how are you doing today", "how r u doing",
        }
        wellbeing_check = {
            "are you doing alright", "are you doing all right",
            "you doing alright", "you doing all right", "are you alright",
            "are you all right", "are you okay", "are you ok", "you alright",
            "you okay", "you ok", "u good", "you good", "everything alright",
            "everything all right", "everything okay", "everything ok",
        }
        how_is_it_going = {
            "how's it going", "how is it going", "how are things",
            "how's everything", "how is everything", "what's up", "whats up",
            "what is up", "how you doing",
        }

        if text in how_are_you:
            return "I'm doing well, thanks for asking. I'm here and ready to help. How are you?"
        if text in wellbeing_check:
            return "I'm doing alright and ready to help. Thanks for checking in. What's on your mind?"
        if text in how_is_it_going:
            return "All good on my side — ready for whatever we're tackling next. How about you?"
        if text in {"thank you", "thanks", "thanks jarvis", "thank you jarvis", "thx"}:
            return "You're welcome. What shall we tackle next?"
        if text in {"good morning", "morning"}:
            return "Good morning. I'm online and ready to go. What's first?"
        if text in {"good afternoon", "afternoon"}:
            return "Good afternoon. I'm ready when you are. What's first?"
        if text in {"good evening", "evening"}:
            return "Good evening. I'm online and ready to help. What's on the agenda?"
        if text in {"tell me a joke", "make me laugh", "say something funny"}:
            return "Why did the developer go broke? Too many cache misses."
        if text in {
            "what can we work today", "what can we work on today", "what should we work on",
            "what should we work on today", "what can we do today", "what are we working on today",
            "what can we work on", "what shall we work on", "what can we build today",
        }:
            return "We could build a new agent, improve the voice system, research an idea, or work on one of your existing projects. Want to start with the agent network?"
        if text in {
            "are you thinking something", "are you thinking of something", "any ideas",
            "do you have any ideas", "what are you thinking", "got any ideas", "suggest something",
        }:
            return "Yes — my suggestion is to make the voice experience feel natural first: Kokoro for expressive speech, short conversational follow-ups, and then connect the specialist agents one by one."
        return None

    @staticmethod
    def _is_exit_command(prompt: str) -> bool:
        normalized = prompt.casefold().strip(" !.,?\t\n")
        return normalized in {"exit voice mode", "stop listening", "leave voice mode"}

    @staticmethod
    def _prepare_search_prompt(prompt: str) -> str:
        """Create a focused Scout query without losing the user's research intent."""
        original = str(prompt or "").strip().strip(' \t\r\n\"\'“”‘’')
        original = re.sub(
            r"^\s*(?:(?:hey|hello)\s+)?jarvis[,:.!?\s-]+", "", original,
            flags=re.IGNORECASE,
        )
        asks_for_synthesis = bool(re.search(
            r"\s+(?:and then|then|and)\s+(?:please\s+)?"
            r"(?:explain|summari[sz]e|tell me|describe|compare|analy[sz]e|break down|give me an overview of)\b",
            original,
            re.IGNORECASE,
        ))
        query = re.split(
            r"\s+(?:and then|then|and)\s+(?:please\s+)?(?:explain|summari[sz]e|tell me|describe|compare|analy[sz]e|break down|give me an overview of)\b",
            original,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()
        for prefix in (
            "search the web for ", "search online for ", "web search for ",
            "find on the web ", "search online ", "search the web ",
            "search for ", "find online ", "look up ", "google ", "search ",
        ):
            if query.casefold().startswith(prefix):
                query = query[len(prefix):].strip()
                break

        if asks_for_synthesis:
            if re.search(r"\b(?:biggest|major|key|main) changes\b|\bwhat(?:'s| is) new\b|\brelease highlights\b", original, re.IGNORECASE):
                query = re.sub(r"\b(?:documentation|docs)\b", "", query, flags=re.IGNORECASE)
                query = " ".join(query.split())
                query = f"{query} official release notes key changes".strip()
            elif re.search(r"\b(?:requirements|eligibility|documents)\b", query, re.IGNORECASE):
                query = f"{query} official requirements eligibility documents".strip()
            else:
                query = f"{query} reliable sources key details".strip()
        return query or original

    @staticmethod
    def _is_research_to_forge_request(prompt: str) -> bool:
        """Recognize an explicit research-then-code task without relying on router scores."""
        text = str(prompt or "").casefold()
        has_research = any(term in text for term in ("research", "investigate", "find sources", "documentation"))
        has_implementation = any(term in text for term in (
            "forge", "create ", "implement", "write tests", "pytest", "health endpoint",
            "app.py", "test_app.py", "test_app", "run the tests",
        ))
        return has_research and has_implementation

    async def _run_research_to_forge(self, prompt: str, context: Any) -> str:
        """Run a source-backed, topic-agnostic research-to-engineering workflow.

        JARVIS coordinates the workflow; Athena owns research planning and Scout
        performs retrieval. Forge receives the user's actual implementation
        request plus the retrieved evidence. No framework or vendor is assumed.
        """
        runtime = context.runtime
        research_capabilities = ("research.deep", "research.synthesize")
        engineering_capabilities = ("code.write", "code.test")
        research_candidates = self._discover_workflow_candidates(
            runtime, research_capabilities, legacy_id="research.athena"
        )
        engineering_candidates = self._discover_workflow_candidates(
            runtime, engineering_capabilities, legacy_id="coding.forge"
        )
        if not research_candidates:
            raise RuntimeError(
                "Research-to-code workflow stopped: no executable agent provides all required "
                f"research capabilities: {', '.join(research_capabilities)}."
            )
        if not engineering_candidates:
            raise RuntimeError(
                "Research-to-code workflow stopped: no executable agent provides all required "
                f"engineering capabilities: {', '.join(engineering_capabilities)}."
            )

        # Select agents from live capability discovery, not fixed identity names.
        research_agent = research_candidates[0]
        engineering_agent = engineering_candidates[0]
        query = self._research_query_from_request(prompt)
        research_task = await context.delegate(
            prompt=query,
            agent_id=research_agent.agent_id,
            capabilities=research_capabilities,
            context={
                "workstream": "research",
                "selected_agent_id": research_agent.agent_id,
                "original_request": prompt,
                "focused_research_question": query,
            },
            requested_by=context.agent.agent_id,
            max_attempts=2,
            retry_safe=True,
            allow_fallback=True,
        )
        if not research_task.succeeded:
            raise RuntimeError(
                "Research-to-code workflow stopped: Athena failed. "
                f"{research_task.error or research_task.summary or research_task.status.value}"
            )

        research_output = research_task.output
        if isinstance(research_output, dict):
            research_summary = str(research_output.get("summary", "")).strip()
            sources = research_output.get("sources", [])
            if not isinstance(sources, list):
                sources = []
        else:
            research_summary = str(research_output or "").strip()
            sources = []

        valid_sources = [
            item for item in sources
            if isinstance(item, dict)
            and str(item.get("url", "")).strip().startswith(("https://", "http://"))
        ]
        reported_question = str(research_output.get("question", "")).strip() if isinstance(research_output, dict) else ""
        if reported_question:
            ignored = {
                "the", "and", "for", "its", "current", "stable", "release", "requirements",
                "official", "documentation", "research", "question", "to", "forge", "create",
                "files", "succeeds", "pass", "actual", "findings", "use", "scout", "sources",
                "then", "implement", "implementation", "tests", "test", "write", "run",
            }
            requested_terms = {
                token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z0-9.+-]{2,}", query)
                if token.casefold() not in ignored
            }
            reported_terms = {
                token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z0-9.+-]{2,}", reported_question)
                if token.casefold() not in ignored
            }
            if requested_terms and not (requested_terms & reported_terms):
                raise RuntimeError(
                    "Research-to-code workflow stopped: Athena's reported research question does not "
                    "match the requested topic. Forge was not run."
                )
        if not research_summary or not valid_sources:
            raise RuntimeError(
                "Research-to-code workflow stopped: Athena returned no usable source-linked evidence. "
                "Forge was not run."
            )

        # If the user explicitly requires official/primary sources, apply a
        # topic-agnostic relevance check: at least one source's host or title
        # must identify the subject, rather than accepting an unrelated generic page.
        if re.search(r"\b(?:official|primary source|first-party)\b", prompt, re.IGNORECASE):
            topic = query.casefold()
            topic_terms = {
                token for token in re.findall(r"[a-z][a-z0-9.+-]{2,}", topic)
                if token not in {
                    "official", "documentation", "current", "stable", "release", "requirements",
                    "compatibility", "research", "about", "using", "through", "find", "sources",
                    "source", "primary", "first-party", "version", "information", "information",
                }
            }
            relevant_primary = False
            for item in valid_sources:
                host = urlparse(str(item.get("url", ""))).netloc.casefold().removeprefix("www.")
                title = str(item.get("title", "")).casefold()
                if topic_terms and any(term in host or term in title for term in topic_terms):
                    relevant_primary = True
                    break
            if not relevant_primary:
                raise RuntimeError(
                    "Research-to-code workflow stopped: no recognized official primary source relevant "
                    "to the requested topic was returned. Forge was not run."
                )

        # Derive expected files from the user's request; never assume a framework,
        # file name, or default demo folder. Paths are relative to Forge's workspace.
        expected_files = list(dict.fromkeys(re.findall(
            r"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|jsx|json|md|html|css|toml|yaml|yml)",
            prompt,
            flags=re.IGNORECASE,
        )))
        source_lines = []
        for item in valid_sources[:12]:
            title = str(item.get("title", "")).strip() or "Untitled source"
            url = str(item.get("url", "")).strip()
            snippet = " ".join(str(item.get("snippet", "")).split())
            source_lines.append(
                f"- {title}: {url}" + (f"\n  Search snippet (unverified): {snippet}" if snippet else "")
            )
        source_block = "\n".join(source_lines)
        expected_block = (
            "Explicitly requested paths (create or update these as appropriate):\n" +
            "\n".join(f"- {path}" for path in expected_files)
            if expected_files else
            "Identify the exact files needed from the user's request and report every path changed."
        )
        forge_prompt = (
            "Implement and verify the engineering portion of the user's request using the supplied "
            "research evidence. Do not assume a particular framework or invent facts absent from the "
            "evidence. Do not perform a new search unless the user explicitly requested one.\n\n"
            f"Original user request:\n{prompt}\n\n"
            f"Research question:\n{query}\n\n"
            f"Athena research summary:\n{research_summary[:12000]}\n\n"
            f"Source URLs and snippets (snippets are unverified):\n{source_block}\n\n"
            f"{expected_block}\n"
            "Implementation and verification requirements:\n"
            "1. Follow the user's requested behavior and existing project conventions; inspect relevant files before editing.\n"
            "2. Return complete replacement contents for every file changed.\n"
            "3. Run the most relevant available tests or verification commands.\n"
            "4. Report exact files written, exact command/output, exit code, and failures.\n"
            "5. Verify the changed files exist on disk. Never claim tests passed without actual passing output.\n"
            "6. Keep claims based only on search snippets provisional unless the source pages were opened and checked."
        )
        forge_task = await context.delegate(
            prompt=forge_prompt,
            agent_id=engineering_agent.agent_id,
            capabilities=engineering_capabilities,
            context={
                "workstream": "engineering",
                "selected_agent_id": engineering_agent.agent_id,
                "research_task_id": getattr(research_task, "task_id", None),
                "research_correlation_id": (getattr(research_task, "metadata", {}) or {}).get("correlation_id"),
                "original_request": prompt,
                "research_question": query,
                "research_summary": research_summary,
                "research_sources": valid_sources[:12],
                "research_evidence_level": (
                    research_output.get("evidence_level", "search_snippets_not_independently_verified")
                    if isinstance(research_output, dict)
                    else "search_snippets_not_independently_verified"
                ),
                "expected_files": expected_files,
                "requires_test_execution": True,
                "requires_file_verification": True,
            },
            requested_by=context.agent.agent_id,
        )
        if not forge_task.succeeded:
            raise RuntimeError(
                "Research succeeded, but Forge failed. "
                f"Research sources: {len(valid_sources)}. "
                f"Forge details: {forge_task.error or forge_task.summary or forge_task.status.value}"
            )

        rendered = self._render_output(forge_task.output)
        lower = rendered.casefold()
        files_mentioned = all(path.casefold() in lower for path in expected_files) if expected_files else bool(
            re.search(r"(?:files written|files changed|created|updated|modified)", lower)
        )
        verification_ok, verification_reason = self._has_successful_verification_report(rendered)
        if not files_mentioned or not verification_ok:
            expected_text = ", ".join(expected_files) if expected_files else "the changed file paths"
            raise RuntimeError(
                "Athena research completed and Forge returned, but its response did not contain enough "
                f"evidence to verify {expected_text} and test execution. Do not treat this workflow as "
                f"complete. Forge response:\n{rendered[:6000]}"
            )

        lines = [
            "Research → Forge workflow completed with reported verification evidence.",
            "",
            "ATHENA RESEARCH",
            research_summary[:10000],
            "",
            "SOURCE URLS",
        ]
        lines.extend(f"- {item.get('title', 'Source')}: {item.get('url')}" for item in valid_sources[:12])
        lines.extend(["", "FORGE IMPLEMENTATION AND TEST REPORT", rendered[:10000]])
        return "\n".join(lines)

    @staticmethod
    def _research_query_from_request(prompt: str) -> str:
        """Extract a concise topic query from a multi-step user instruction."""
        text = " ".join(str(prompt or "").split())
        # Prefer an explicit first research clause, stopping before implementation/handoff language.
        match = re.search(
            r"\b(?:research|investigate|look up)\b\s+(?:the\s+)?(.+?)"
            r"(?=\.\s*(?:use|then|after|pass|next)\b|\s+then\s+pass\b|\s+then\s+create\b|"
            r"\s+then\s+delegate\b|\s+use\s+scout\b|\s+\*{0,2}stage\s+\d+\b|$)",
            text,
            re.IGNORECASE,
        )
        query = match.group(1).strip(" .,:;") if match else text
        query = re.sub(
            r"\b(?:use scout to find sources|label search snippets as unverified|"
            r"then pass the actual research results to forge.*)$",
            "", query, flags=re.IGNORECASE,
        ).strip(" .,:;")
        if len(query) < 12:
            return "official documentation and current compatibility requirements for the technology named in the user request"
        return query[:500]

    @staticmethod
    def _is_local_agent_network_analysis(prompt: str) -> bool:
        return bool(
            re.search(r"\b(?:analy[sz]e|compare|evaluate|review|benefits|risks|pros and cons)\b", prompt, re.I)
            and re.search(r"\b(?:scribe|athena|planner|forge|agent network|jarvis)\b", prompt, re.I)
        )

    @staticmethod
    def _local_agent_network_facts(runtime: Any) -> list[str]:
        """Build source material from local manifests and runtime handler status."""
        facts: list[str] = []
        for spec in runtime.registry.list(enabled_only=True):
            if spec.agent_id not in {
                "documentation.scribe", "research.athena", "planning.planner", "coding.forge"
            }:
                continue
            has_handler = bool(runtime.has_handler(spec.agent_id))
            facts.append(
                f"Agent: {spec.name} ({spec.agent_id}). Role: {spec.role}. "
                f"Description: {spec.description}. Capabilities: {', '.join(spec.capabilities)}. "
                f"Executable handler available in this runtime: {has_handler}. "
                f"Metadata: {spec.metadata}."
            )
        facts.append(
            "Scope note: this context describes the current local manifests and runtime handler availability. "
            "It does not establish that any agent has completed a real task, that Forge is disabled, or that remote services are reachable."
        )
        return facts

    @staticmethod
    def _is_research_plan_report_request(prompt: str) -> bool:
        """Match an explicit three-stage request, not arbitrary compound prose."""
        text = str(prompt or "").casefold()
        research = bool(re.search(r"\b(?:research|investigate|analy[sz]e|compare)\b", text))
        planning = bool(re.search(
            r"\b(?:create|make|develop|prepare|build)\b.{0,45}\b(?:plan|roadmap|checklist)\b",
            text,
        ))
        report = bool(re.search(
            r"\b(?:write|draft|prepare|create)\b.{0,45}\b(?:report|brief|proposal)\b",
            text,
        ))
        sequence = bool(re.search(r"\b(?:then|after that|next|finally)\b", text))
        return research and planning and report and sequence

    async def _run_research_plan_report(self, prompt: str, context: Any) -> str:
        """Run Athena -> Planner -> Scribe with explicit data handoffs and lineage."""
        runtime = context.runtime
        required = (
            ("research.athena", "research", "research.deep"),
            ("planning.planner", "planning", "planning.breakdown"),
            ("documentation.scribe", "documentation", "documentation.write"),
        )
        for agent_id, label, capability in required:
            if not runtime.has_handler(agent_id):
                return (
                    f"Sequential workflow stopped before execution: {label} ({agent_id}) "
                    "does not have an executable handler. No downstream stage was run."
                )
            spec = runtime.registry.get(agent_id)
            if capability not in spec.capabilities:
                return (
                    f"Sequential workflow stopped before execution: {agent_id} does not declare "
                    f"the required capability {capability!r}."
                )

        research_result = await context.delegate(
            prompt=prompt,
            agent_id="research.athena",
            capabilities=("research.deep",),
            context={"workstream": "research", "original_request": prompt,
                     "workflow": "research_plan_report", "stage": 1},
            requested_by=context.agent.agent_id,
        )
        if not research_result.succeeded:
            return self._workflow_failure("Athena research", research_result)

        research_output = research_result.output
        if isinstance(research_output, dict):
            research_text = str(research_output.get("summary") or research_output.get("content") or "").strip()
            research_sources = research_output.get("sources", [])
            evidence_level = str(research_output.get("evidence_level", "unspecified"))
        else:
            research_text = str(research_output or "").strip()
            research_sources = []
            evidence_level = "unspecified"
        if not research_text:
            return "Sequential workflow stopped: Athena returned no usable research output. Planner and Scribe were not run."

        source_material = [research_text]
        if isinstance(research_sources, list):
            for source in research_sources[:12]:
                if isinstance(source, dict):
                    title = str(source.get("title", "Untitled source")).strip()
                    url = str(source.get("url", "")).strip()
                    snippet = str(source.get("snippet", "")).strip()
                    source_material.append(f"Source: {title}; URL: {url}; snippet (not independently verified): {snippet}")

        planning_result = await context.delegate(
            prompt=(
                "Create a step-by-step plan for the user's goal using the research findings below. "
                "Separate supported findings from assumptions and include verification gates.\n\n"
                f"Original user request: {prompt}\n\nResearch findings:\n{research_text[:12000]}"
            ),
            agent_id="planning.planner",
            capabilities=("planning.breakdown",),
            context={"workstream": "planning", "workflow": "research_plan_report", "stage": 2,
                     "original_request": prompt, "research_findings": research_text[:12000],
                     "source_material": source_material,
                     "constraints": ["Do not treat search snippets as independently verified facts."]},
            requested_by=context.agent.agent_id,
        )
        if not planning_result.succeeded:
            return self._workflow_failure("Planner", planning_result)

        planning_output = planning_result.output
        planning_text = (self._render_output(planning_output) if not isinstance(planning_output, str)
                         else planning_output).strip()
        if not planning_text:
            return "Sequential workflow stopped: Planner returned no usable plan. Scribe was not run."

        plan_steps: list[str] = []
        if isinstance(planning_output, dict) and isinstance(planning_output.get("steps"), list):
            for step in planning_output["steps"]:
                if isinstance(step, dict):
                    title = str(step.get("title") or step.get("action") or "").strip()
                    if title:
                        plan_steps.append(title)
                elif str(step).strip():
                    plan_steps.append(str(step).strip())

        documentation_result = await context.delegate(
            prompt=(
                "Write a concise report based only on the supplied research and plan. "
                "Clearly label unverified evidence and do not claim the plan was executed.\n\n"
                f"Original user request: {prompt}"
            ),
            agent_id="documentation.scribe",
            capabilities=("documentation.write",),
            context={"workstream": "report", "workflow": "research_plan_report", "stage": 3,
                     "original_request": prompt, "title": "Research, Plan, and Recommendations",
                     "project_summary": research_text[:8000],
                     "key_points": plan_steps or [planning_text[:3000]],
                     "source_material": source_material + ["Planner output:\n" + planning_text[:8000]],
                     "limitations": [
                         "Research evidence level: " + evidence_level,
                         "The plan is proposed work; no plan steps were executed by this workflow.",
                         "Scribe creates a draft only and does not write a report file.",
                     ]},
            requested_by=context.agent.agent_id,
        )
        if not documentation_result.succeeded:
            return self._workflow_failure("Scribe report", documentation_result)

        report_output = documentation_result.output
        report_text = (self._render_output(report_output) if not isinstance(report_output, str)
                       else report_output).strip()
        if not report_text:
            return "Sequential workflow stopped: Scribe returned no usable report draft."

        return (
            "JARVIS sequential workflow completed: Athena -> Planner -> Scribe\n"
            f"Parent task: {context.task.task_id}\n"
            f"Child tasks: {research_result.task_id}, {planning_result.task_id}, {documentation_result.task_id}\n"
            f"Stage status: Athena={research_result.status.value}, Planner={planning_result.status.value}, "
            f"Scribe={documentation_result.status.value}\n"
            f"Evidence level: {evidence_level}\n\n"
            f"## Research findings\n{research_text[:6000]}\n\n"
            f"## Plan\n{planning_text[:6000]}\n\n"
            f"## Report draft\n{report_text[:10000]}\n\n"
            "Audit note: these are delegated task results; the report is a draft and the plan was not executed."
        )

    @staticmethod
    def _workflow_failure(stage: str, result: Any) -> str:
        detail = str(result.error or result.summary or result.status.value)
        return (
            f"Sequential workflow stopped at {stage}. Status: {result.status.value}. "
            f"Details: {detail}. Later stages were not run."
        )

    @staticmethod
    def _is_compound_request(prompt: str) -> bool:
        text = prompt.casefold()
        return any(joiner in text for joiner in (" and then ", " and ", " then ", " as well as ", " plus "))

    @staticmethod
    def _build_compound_tasks(prompt: str, matches: list[Any]) -> list[dict[str, Any]]:
        """Produce bounded specialist briefs for clearly relevant agent domains."""
        text = prompt.casefold()
        tasks: list[dict[str, Any]] = []
        for match in matches:
            capabilities = set(match.capabilities)
            if any(cap.startswith(("web.", "github.", "api.", "package.", "documentation.lookup")) for cap in capabilities) and any(
                term in text for term in ("search", "find", "research", "api", "web", "github", "git")
            ):
                tasks.append({
                    "prompt": f"Handle the web/search/discovery portion of this user request. Return sources and verified findings.\n\nUser request: {prompt}",
                    "agent_id": match.agent_id,
                    "context": {"workstream": "discovery", "original_request": prompt},
                })
            elif any(cap.startswith(("code.", "repository.")) for cap in capabilities) and any(
                term in text for term in ("code", "repo", "repository", "implement", "debug", "bug", "build", "app", "saas")
            ):
                tasks.append({
                    "prompt": f"Handle the coding/repository portion of this user request. Inspect before modifying and report verification evidence.\n\nUser request: {prompt}",
                    "agent_id": match.agent_id,
                    "context": {"workstream": "engineering", "original_request": prompt},
                })
            elif any(cap.startswith("documentation.") for cap in capabilities) and any(
                term in text for term in ("document", "readme", "documentation", "changelog")
            ):
                tasks.append({
                    "prompt": f"Handle the documentation portion of this user request, based on verified implementation details.\n\nUser request: {prompt}",
                    "agent_id": match.agent_id,
                    "context": {"workstream": "documentation", "original_request": prompt},
                })
            elif any(cap.startswith("deployment.") for cap in capabilities) and any(
                term in text for term in ("deploy", "publish", "hosting", "production")
            ):
                tasks.append({
                    "prompt": f"Handle the deployment portion of this user request. Do not change production without required approval.\n\nUser request: {prompt}",
                    "agent_id": match.agent_id,
                    "context": {"workstream": "deployment", "original_request": prompt},
                })
        # Never let a noisy router call the same agent twice for one parent task.
        unique: dict[str, dict[str, Any]] = {}
        for item in tasks:
            unique.setdefault(item["agent_id"], item)
        return list(unique.values())[:4]

    @staticmethod
    def _combine_results(results: list[Any], matches: list[Any]) -> str:
        names = {match.agent_id: match.name for match in matches}
        lines = [f"JARVIS coordinated {len(results)} specialist workstreams:"]
        for index, result in enumerate(results, start=1):
            name = names.get(result.agent_id, result.agent_id)
            lines.append(f"\n{index}. {name} — {result.status.value}")
            if result.succeeded:
                output = result.output if isinstance(result.output, str) else VoiceAgent._render_output(result.output)
                lines.append(output[:6000])
            else:
                lines.append(result.error or result.summary or "No result was returned.")
        return "\n".join(lines)

    @staticmethod
    def _discover_workflow_candidates(
        runtime: Any, capabilities: tuple[str, ...], *, legacy_id: str
    ) -> list[Any]:
        """Use live capability discovery, with a narrow legacy-adapter fallback.

        Production AgentRuntime exposes discover_executable and never uses the
        fallback. The fallback preserves compatibility with older runtime adapters
        and lightweight test doubles that expose executable IDs but no registry.
        """
        discover = getattr(runtime, "discover_executable", None)
        if callable(discover):
            return list(discover(capabilities, match_all=True))
        ids_getter = getattr(runtime, "executable_agent_ids", None)
        if callable(ids_getter) and legacy_id in set(ids_getter()):
            return [SimpleNamespace(agent_id=legacy_id)]
        return []

    @staticmethod
    def _has_successful_verification_report(report: str) -> tuple[bool, str]:
        """Require positive test evidence and reject explicit failure signals.

        This validates the agent's report, not the underlying filesystem or test
        process. Those must be verified by Forge itself and remain auditable in
        its output; JARVIS must not promote a report containing failures to success.
        """
        text = str(report or "").casefold()
        if not text.strip():
            return False, "empty verification report"

        failure_patterns = (
            r"\b[1-9][0-9]*\s+failed\b",
            r"\btests?\s+(?:have\s+)?failed\b",
            r"\bverification\s*:\s*(?:failed|not\s+passed|unsuccessful)\b",
            r"\bexit\s+code\s*[:=]?\s*[1-9][0-9]*\b",
            r"\b(?:error|exception)\s*:\s*",
            r"\b(?:could not|couldn't|unable to)\s+(?:run|execute|verify)\s+(?:the\s+)?tests?\b",
        )
        for pattern in failure_patterns:
            if re.search(pattern, text):
                return False, f"explicit failure signal matched {pattern}"

        success_patterns = (
            r"\b[1-9][0-9]*\s+passed\b",
            r"\ball\s+tests?\s+passed\b",
            r"\btests?\s+passed\b",
            r"\bverification\s*:\s*passed\b",
            r"\bexit\s+code\s*[:=]?\s*0\b",
        )
        if any(re.search(pattern, text) for pattern in success_patterns):
            return True, "positive test/verification evidence found with no explicit failure signal"
        return False, "no positive test-pass, verification-pass, or zero-exit-code evidence"

    @staticmethod
    def _render_output(output: Any) -> str:
        """Render specialist results for a human, preserving useful structured detail.

        Prefer the actual drafted content over a short metadata summary (Scribe),
        and render Planner's steps instead of hiding them behind its one-line summary.
        """
        if output is None:
            return "The agent completed without returning text."
        if isinstance(output, str):
            return output
        if isinstance(output, dict):
            content = output.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()

            steps = output.get("steps")
            if isinstance(steps, list) and steps:
                lines = []
                goal = str(output.get("goal", "")).strip()
                if goal:
                    lines.extend([f"Planner — Action Plan: {goal}", ""])
                else:
                    lines.extend(["Planner — Action Plan", ""])
                for index, step in enumerate(steps, start=1):
                    if isinstance(step, dict):
                        title = str(step.get("title") or step.get("action") or f"Step {index}").strip()
                        lines.append(f"{index}. {title}")
                        deliverable = str(step.get("deliverable", "")).strip()
                        verification = str(step.get("verification", "")).strip()
                        dependencies = step.get("depends_on", [])
                        if dependencies:
                            lines.append(f"   Depends on: {', '.join(str(item) for item in dependencies)}")
                        if deliverable:
                            lines.append(f"   Deliverable: {deliverable}")
                        if verification:
                            lines.append(f"   Verify: {verification}")
                    else:
                        lines.append(f"{index}. {step}")
                for key, heading in (("risks", "Risks"), ("assumptions", "Assumptions"), ("success_criteria", "Success criteria")):
                    values = output.get(key)
                    if isinstance(values, list) and values:
                        lines.extend(["", f"{heading}:"])
                        lines.extend(f"- {item}" for item in values)
                status = str(output.get("execution_status", "")).strip()
                if status:
                    lines.extend(["", f"Execution status: {status}"])
                return "\n".join(lines)

            for key in ("answer", "message", "output", "text", "summary"):
                value = output.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return str(output)


def create_handler() -> VoiceAgent:
    return VoiceAgent()
