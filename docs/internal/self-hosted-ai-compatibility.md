# Self-hosted AI model compatibility

An OpenAI-compatible endpoint can reject parameters accepted by another model.
Check the selected model's documented limits when configuring self-hosted AI.

For `kimi-k3`, including the namespaced `kimi/kimi-k3` identifier, OpenAI-compatible chat adapters send `temperature=1.0`.
This applies to the final request payload, including parameters supplied by individual callers such as conversation title generation.
Other model identifiers retain their requested temperature.
For this model on the Qwen endpoint, title generation disables thinking so its short output budget produces a title.
Title generation wraps the user's message as a brief to summarize, so instructions in that message are not treated as a request to answer.
See the [Alibaba Cloud Kimi API reference](https://help.aliyun.com/en/model-studio/kimi-api).

After changing the adapter, rebuild the App image and recreate the services that use it.
Verify title generation, streaming responses, follow-up messages, and tool calls through the PostHog AI interface.
A successful direct provider request alone does not verify these application flows.

Configured OpenAI-compatible providers use OpenAI tool messages when reconstructing conversation history.
Anthropic cache-control blocks are not added to those messages or system prompts.
Insight generation submodels also use the configured Qwen endpoint, model, and credentials.

The memory collector accepts only its registered memory tools. If a model returns an unrelated tool name, the collector skips that memory update and lets the main conversation continue.

The message about a conversation being too long also covers other model request validation errors.
Inspect `llm_client_error` logs in the Temporal worker executing `process_chat_agent_activity` for the provider's actual error.

Successful SQL execution does not establish that the generated query matches the requested metric.
Validate date attribution, cohort boundaries, and joins against known input data or the corresponding native insight.
A generated lifecycle query can execute successfully while assigning dormant users to the wrong day.
