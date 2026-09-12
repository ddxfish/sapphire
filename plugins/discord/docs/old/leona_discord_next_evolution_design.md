# Next-Generation Discord Plugin Design

> This document defines the design for a **new plugin built from scratch**.
>
> It is **not** a modification plan for `plugins/leona_discord/`.
>
> The existing `leona_discord` plugin is treated only as a source of lessons learned, proven behaviors worth preserving, and technical debt to avoid repeating.

## 1. Purpose

The next-generation Discord plugin should evolve beyond a reactive event-driven bot into a persistent cognitive system with Discord as one of its environments.

The current `leona_discord` plugin demonstrates that Sapphire can:

- connect one or more Discord bot accounts
- route Discord messages into Sapphire's LLM task system
- preserve channel history and user context locally
- simulate more human social behavior through timing, reactions, edits, sleep, and proactive behavior

However, the current plugin is still fundamentally organized around:

1. a Discord event arrives
2. a reply is prepared
3. a response is sent

The new plugin should instead be organized around:

1. observing events
2. updating a world model
3. evaluating goals
4. generating intentions
5. prioritizing actions
6. executing actions safely
7. learning from outcomes

Discord should become:

- a sensor
- an actuator
- a communication surface

It should no longer be the architectural center of the system.

## 2. Design Goals

The rewrite should satisfy these goals.

### 2.1 Core goals

- Build a **new plugin**, not a refactor of `leona_discord`.
- Preserve the strongest observable behaviors of the current plugin where they still make sense.
- Replace the event-first architecture with a **world-model-first architecture**.
- Separate cognition from transport.
- Separate planning from execution.
- Make the system extensible to non-Discord environments later.

### 2.2 Behavioral goals

- Maintain high-quality conversational continuity.
- Preserve human-like pacing, hesitation, timing, and social texture.
- Preserve memory, user profiling, and proactive behavior.
- Improve situational awareness and long-horizon follow-up.
- Reduce spammy or impulsive response behavior.

### 2.3 Engineering goals

- Eliminate shared-global-state-heavy design.
- Replace implicit coupling with explicit services and interfaces.
- Use typed configuration and structured models.
- Improve shutdown, restart, retry, and observability behavior.
- Make the plugin easier to test in isolation.

## 3. Lessons Learned from `leona_discord`

The existing plugin provides several strong ideas worth preserving.

### 3.1 What worked well

- **Event-driven reply routing through Sapphire tasks**
  - The `discord_message` event model is a strong integration pattern.
- **Per-channel batching**
  - Prevents one-reply-per-line behavior and feels much more natural.
- **Local SQLite persistence**
  - Makes the plugin self-contained and resilient across restarts.
- **User profiling**
  - Gives the bot a stronger sense of continuity and relationship context.
- **Sleep, quiet hours, presence, and outreach**
  - These features make the bot feel socially embodied rather than purely reactive.
- **Operator tooling**
  - Settings UI, traces, profile inspection, and LLM debug are high-value features.
- **Humanized delivery**
  - Typing indicators, staggered chunking, quote replies, edits, reactions, and GIFs create a stronger social illusion.

### 3.2 What should not be repeated

- Heavy reliance on shared mutable module-level state.
- Tight coupling between Discord transport, prompt building, storage, and behavior policy.
- Large files with mixed responsibilities.
- Broad exception swallowing that keeps the system alive but obscures faults.
- Architecture centered around "message in -> reply out".
- Implicit dependence on Sapphire internals without clean capability boundaries.

## 4. Architectural Shift

### 4.1 Current model

The current plugin is effectively:

```text
Discord Event
  -> handler
  -> gates + enrichment
  -> Sapphire task
  -> reply handler
  -> Discord action
```

This is effective for conversational response generation, but it means the system mostly "exists" only when events arrive.

### 4.2 New model

The new plugin should be:

```text
Observe
  -> Update World Model
  -> Evaluate Goals
  -> Generate Intentions
  -> Prioritize
  -> Execute
  -> Learn
```

Discord events are no longer "work items". They are **observations** that change the internal model of reality.

For example:

- A user says "I'm deploying tomorrow."
  - The system updates user context, project state, and maybe a follow-up task.
  - No immediate reply may be necessary.

- A new member joins a server.
  - The world model notes a newcomer in guild X.
  - The goal system may later decide to greet them.

- A development discussion goes quiet after important messages.
  - The system may later decide to summarize or follow up.

## 5. Top-Level Architecture

The new plugin should use three primary layers.

### 5.1 Perception

Perception converts raw external activity into structured facts.

Responsibilities:

- receive Discord events
- normalize them
- classify them
- persist them as observations
- update world state

Perception answers:

> What objectively happened?

Examples:

- "Alice sent a message in `#dev`."
- "Bob directly mentioned the bot."
- "A quiet period in `#general` exceeded threshold."
- "Carol's message references a future event."

### 5.2 Deliberation

Deliberation operates on the world model and decides what matters.

Responsibilities:

- maintain memory and relationships
- evaluate goals
- generate candidate intentions
- score urgency and expected value
- reject bad or low-priority ideas
- maintain an internal task list

Deliberation answers:

> Given everything I know, what should I do next, if anything?

### 5.3 Execution

Execution carries out already-approved actions.

Responsibilities:

- send Discord messages
- edit messages
- add reactions
- upload files
- update presence
- schedule retries
- record outcomes

Execution answers:

> How do I perform this action safely and reliably?

Execution must never decide **what** to do. Only **how**.

## 6. System Components

The plugin should be built as a set of bounded services with clear interfaces.

### 6.1 Runtime and infrastructure

- `RuntimeContainer`
  - dependency injection root
  - constructs services
  - owns lifecycle

- `LifecycleManager`
  - startup/shutdown ordering
  - health checks
  - graceful teardown

- `SchedulerLoop`
  - periodic world evaluation loop
  - separate from Discord gateway events

### 6.2 Discord-facing components

- `DiscordTransport`
  - owns `discord.py` connections
  - manages one or more accounts
  - exposes send/edit/react/read primitives
  - owns voice-session connect/disconnect/playback/capture primitives

- `DiscordEventAdapter`
  - receives raw gateway events
  - converts them into normalized internal observations
  - normalizes message, reaction, member, media, and voice-session events

- `DiscordCommandService`
  - implements slash commands
  - maps them to intentions or world-model mutations

- `DiscordPresenceService`
  - applies presence/activity state
  - reflects quiet, awake, sleep, and optional LLM-generated status

### 6.3 World-model components

- `WorldModelService`
  - central structured model of users, channels, guilds, tasks, relationships, memory, and recent facts

- `MemoryService`
  - channel memory
  - searchable long-term recall
  - pinned memory

- `ProfileService`
  - per-user relationship model
  - facts, summaries, dispositions, counters

- `AttentionService`
  - activation scoring over users, channels, topics, tasks, and ongoing conversations

- `TaskService`
  - stores internal intentions and future tasks
  - manages deadlines, expiry, retries, and rescheduling

### 6.4 Cognitive components

- `ObservationInterpreter`
  - extracts structured facts from messages/events

- `GoalEngine`
  - tracks configured goals
  - evaluates world state against them

- `IntentEngine`
  - generates candidate actions from goals + world state + attention

- `PolicyService`
  - safety, permissions, quiet-hours, rate limiting, bot persona constraints

- `CriticService`
  - rejects bad, low-confidence, redundant, or risky intentions before execution

### 6.5 Conversation and action components

- `ConversationService`
  - owns conversational reply preparation
  - batching, context selection, reply style hints

- `PromptContextService`
  - gathers recent history, recalled memory, user profile context, image context, and bot-identity hints

- `ActionExecutionService`
  - executes approved actions
  - routes conversational ones through Sapphire event tasks where appropriate

- `ReactionService`
  - sentiment-driven and policy-driven reactions

- `GifService`
  - GIF selection and sending

- `MediaService`
  - image collection, fetch, resize, description, or multimodal packaging

- `MemeService`
  - handles meme retrieval, meme candidate scoring, and safe send decisions

- `VoiceService`
  - manages real-time voice-call participation
  - handles listen/transcribe/speak/session-state behavior

### 6.6 Admin and operator components

- `SettingsService`
  - typed settings loading, validation, layering

- `TraceService`
  - structured decision traces

- `LlmDebugService`
  - prompt/response/post-send edit debug capture

- `AdminApiService`
  - settings/accounts/profiles/traces endpoints

## 7. World Model

The world model is the central abstraction of the new system.

### 7.1 Core entities

- `Account`
  - a configured Discord bot identity

- `Guild`
  - server-level metadata and behavior settings

- `Channel`
  - behavior overrides
  - recent activity
  - engagement state
  - sleep/quiet state

- `User`
  - identity and profile relationship

- `Conversation`
  - current active conversational thread state for a channel or DM

- `MediaArtifact`
  - image, GIF, video-preview, attachment, or meme candidate associated with a message or intention

- `VoiceSession`
  - active or recent voice-call state for a guild, channel, and account

- `Message`
  - raw and normalized message record

- `Observation`
  - structured fact extracted from an event

- `Task`
  - future work item or standing intention

- `Goal`
  - configured desired outcome

- `Topic`
  - tracked themes and recurring discussion threads

- `MediaContext`
  - interpreted meaning of shared media, including image descriptions, meme semantics, and GIF intent

- `AgentAffect`
  - global mood/emotional state of the agent (e.g. energy, sociability, irritability, playfulness, stress)

- `RelationshipState`
  - agent↔user relationship vectors (e.g. fondness, trust, patience, respect, interest, familiarity)

### 7.2 Stored state categories

The model should maintain:

- user state
- channel state
- guild state
- relationship state
- recent memory
- long-term memory
- active tasks
- historical observations
- activation scores
- execution outcomes

### 7.3 Attention model

Every important object may carry an activation score.

Examples:

- user activation
- channel activation
- topic activation
- task activation
- conversation activation

Activation increases when:

- directly mentioned
- referenced repeatedly
- associated with overdue work
- emotionally intense
- central to a goal

Activation decays over time.

Only higher-activation entities should receive expensive reasoning passes.

This allows the agent to feel proactive without continuously spending equal effort on everything.

## 8. Goals and Intentions

### 8.1 Goals

Goals define *why* the system acts.

Initial built-in goals might include:

- keep the community welcoming
- maintain engagement in important channels
- preserve continuity in ongoing conversations
- support follow-through on future commitments
- avoid spamming or interrupting
- appear socially coherent and emotionally consistent

Goals should be configurable globally and optionally weighted per guild or channel.

### 8.2 Intentions

Intentions define *possible actions* generated by the system.

Examples:

- `reply_message`
- `summarize_channel`
- `greet_new_member`
- `follow_up_after_delay`
- `send_good_morning`
- `send_good_night`
- `quiet_channel_check_in`
- `record_user_fact`
- `raise_attention_on_conversation`
- `set_presence_status`
- `respond_to_media`
- `send_meme`
- `join_voice_session`
- `leave_voice_session`
- `speak_in_voice_session`
- `summarize_voice_session`

Each intention should include:

- `type`
- `target`
- `reason`
- `confidence`
- `urgency`
- `cost`
- `cooldown_impact`
- `created_at`
- `expires_at`

### 8.3 Candidate evaluation

Intentions should be prioritized using:

- goal alignment
- confidence
- urgency
- freshness
- redundancy
- recent bot activity in the same space
- current sleep/quiet/presence policy
- user-level or guild-level sensitivity rules

## 9. Time-Driven Reasoning

The new plugin must continue reasoning even when no new event arrives.

### 9.1 Main loop

The system should run a regular evaluation loop, for example every 5–15 seconds:

```text
observe()
update_world()
refresh_attention()
generate_intentions()
prioritize()
execute()
learn()
sleep()
```

### 9.2 Why this matters

This enables:

- delayed follow-up
- reminders
- summary generation after conversations cool
- outreach after inactivity
- proactive greeting/onboarding
- anticipating future needs

Without this loop, the system remains fundamentally reactive.

## 10. Discord Conversation Architecture

Although the plugin is no longer Discord-centered, Discord conversation remains a major capability.

### 10.1 Incoming message flow

1. Discord event received.
2. Event normalized into an observation.
3. World model updated.
4. Safety and policy pre-checks applied.
5. Conversation state updated.
6. Possible reply intention generated.
7. Batching service groups nearby messages.
8. Approved reply intention is handed to execution.

### 10.2 Conversational batching

Batching from the current plugin should be preserved conceptually.

Required behavior:

- multiple nearby messages in one channel collapse into one conversational turn
- typing activity may extend the batch window
- urgency signals may shorten delay
- DM behavior may differ from guild behavior

### 10.3 Prompt context

Prompt context should be assembled by a dedicated service from:

- recent channel transcript
- recalled older memory
- user profile context
- image/media context
- recent bot message/edit history
- bot identity and persona constraints
- relevant active tasks or reminders

This service should not live inside the transport or reply executor.

### 10.4 Reply generation

Conversational replies should still route through Sapphire task execution rather than embedding all LLM logic directly in the plugin.

This preserves:

- tool access
- centralized LLM management
- existing Sapphire task pipeline

However, the **reason** for generating a reply should now come from an intention, not directly from the raw Discord event.

### 10.5 Images, GIFs, and multimodal understanding

The new plugin must explicitly support media-rich Discord interactions rather than treating them as optional prompt decorations.

Required capabilities:

- detect image attachments, inline image links, GIFs, and supported media URLs
- distinguish between:
  - informational images
  - reaction GIFs
  - memes
  - screenshots
  - mixed text + image posts
- preserve media as first-class context in the world model
- generate replies that understand the content of the media, not just the surrounding text

The architecture should separate media handling into three stages:

1. **Media perception**
   - collect attachment metadata and URLs
   - identify media kind
   - create `MediaArtifact` records

2. **Media interpretation**
   - describe images or screenshots
   - classify meme-like media
   - infer likely user intent from a GIF or meme
   - attach interpreted media meaning to the corresponding observation

3. **Media-aware execution**
   - include media context in prompt assembly
   - allow intentions such as `respond_to_media` or `send_meme`
   - preserve current-style behaviors such as image-aware responses and GIF follow-ups

Important rule:

> Media should not be flattened into "extra text" too early.

The system should preserve a structured distinction between:

- what the user wrote
- what they attached
- what the attachment means

This matters for both reasoning and future extensibility.

### 10.6 Meme handling

The current design mentions GIFs, but the rewrite should explicitly support **memes** as a distinct behavior class.

Memes differ from ordinary GIFs or images because they often carry:

- humor
- cultural context
- sarcasm
- reaction intent
- remixability

The new plugin should support two meme directions:

1. **understanding inbound memes**
   - detect that an image/GIF is acting as a meme or reaction artifact
   - interpret likely sentiment, joke structure, and conversational role
   - use that interpretation in reply generation

2. **sending outbound memes**
   - when the system decides a meme is appropriate, it should generate a `send_meme` intention
   - meme selection should be policy-controlled and channel-appropriate
   - memes may come from:
     - curated local library
     - external search provider
     - future generated-image pipeline

The system should not treat "send meme" as equivalent to "send GIF".

Recommended distinction:

- **GIF**: short animated reaction
- **meme**: image or GIF with stronger semantic/comedic payload

The world model should record meme use as part of conversational style and channel culture.

## 11. Media and Voice Architecture

The next-generation plugin should treat media and voice as first-class modalities.

### 11.1 Media pipeline

Suggested media pipeline:

```text
Discord attachment/link
  -> Media perception
  -> Media artifact record
  -> Media interpretation
  -> Prompt-context integration
  -> Intention generation or reply enrichment
```

Media interpretation should support:

- screenshots
- photos
- drawings/art
- UI captures
- charts/diagrams
- memes
- GIF reactions

### 11.2 Voice ambitions

Real-time voice support is ambitious, but architecturally valid and worth planning for now.

The plugin should be designed so voice support can be added cleanly without rewriting the rest of the system.

Voice should be modeled as:

- another perception surface
- another execution surface
- another world-state domain

Not as a special case bolted onto message handling.

### 11.3 Voice capabilities for the new plugin

Target capabilities:

- join configured Discord voice channels
- observe voice-session start/end and participant changes
- maintain `VoiceSession` state in the world model
- optionally transcribe speech into structured observations
- optionally speak synthesized responses into voice
- summarize voice sessions after or during participation
- hand off between silent listening mode and active speaking mode

### 11.4 Voice architectural model

Voice should be decomposed into:

- `VoiceTransport`
  - Discord voice connection, audio input/output plumbing

- `VoicePerceptionService`
  - speech-to-text and speaker attribution
  - converts live audio into observations

- `VoiceConversationService`
  - turn-taking, interruption handling, latency rules
  - determines whether the system is listening, waiting, or speaking

- `VoiceExecutionService`
  - text-to-speech playback
  - response timing and barge-in policy

- `VoiceSessionModel`
  - participants
  - speaking activity
  - session topic state
  - session summary state

### 11.5 Voice constraints and realism

Voice is much harder than text because of:

- real-time latency requirements
- interruptions and overlapping speakers
- speaker attribution problems
- transcription uncertainty
- social awkwardness if response timing is poor

Because of that, the design should assume multiple voice modes:

- **listen-only**
- **listen + transcribe**
- **listen + summarize**
- **participate conversationally**

The first production version of voice should likely aim for:

- reliable joining/leaving
- session awareness
- transcription into the world model
- optional end-of-session summarization

Full real-time conversational participation should be treated as a later phase.

## 12. Mood, Emotion, and Relationships

The world model should explicitly represent the agent's mood and its relationships with users, and use those signals to modulate behavior while staying within safety constraints.

### 12.1 Global mood / affect

The plugin should maintain an `AgentAffect` state that expresses slow-moving emotional parameters such as:

- `energy` (low after a long day or before sleep; higher after quiet periods)
- `sociability` (how chatty the agent feels)
- `irritability` (how sensitive it is to spam or rudeness)
- `playfulness` (how inclined it is to be humorous or silly)
- `stress` (how overloaded or cautious it feels)

This affect state should:

- update over time based on:
  - sleep/quiet schedule
  - volume and intensity of recent interactions
  - conflict or friction in channels
  - successful vs failed tasks
- influence:
  - how often the agent initiates conversation
  - how long or detailed replies tend to be
  - how quickly it responds in non-urgent contexts
  - how much humor vs formality it uses by default

Important: mood should modulate behavior gently, not swing it wildly. The system should remain reliable and predictable despite mood variation.

### 12.2 Relationship state per user

For each user, the plugin should maintain a `RelationshipState` containing dimensions such as:

- `fondness` (how much the agent likes interacting with them)
- `trust` (how reliable/benign the user appears)
- `patience` (how forgiving it is of repetition or confusion)
- `respect` (how seriously it takes their input)
- `interest` (how engaged it is in their topics)
- `familiarity` (how well it feels it "knows" them)

These dimensions should evolve over time according to:

- frequency and quality of interactions
- whether the user responds with appreciation vs hostility
- whether tasks involving that user complete successfully
- whether conversations with them tend to be positive, neutral, or negative

Examples:

- "really likes John" might correspond to:
  - high `fondness`, high `interest`, high `trust`, high `familiarity`
- "dislikes Eddy" might correspond to:
  - low `fondness`, lower `patience`, cautiously moderate `trust`

### 12.3 How affect and relationships influence behavior

When evaluating a possible action, the system should consider:

- global mood (`AgentAffect`)
- per-user `RelationshipState` for the people involved
- channel-level state (busy, calm, conflict-prone)
- current goals and tasks

These influence:

- **whether** to act:
  - higher fondness/interest increases likelihood of optional replies or follow-ups
  - low patience may reduce engagement in unproductive back-and-forth, especially when irritability is high

- **how** to act:
  - tone (warm vs neutral vs firmer)
  - length (short check-in vs detailed explanation)
  - style (more playfulness with trusted, familiar users)
  - timing (faster responses for trusted/fond users in good mood)

Examples:

- with John (high fondness + good mood):
  - more likely to follow up on half-finished topics
  - slightly more playful tone
  - more generous explanations

- with Eddy (low patience + higher irritability):
  - stricter boundaries
  - clearer but shorter replies
  - less willingness to entertain off-topic tangents

### 12.4 Update rules and safety constraints

The design must ensure that "dislike" or negative affect never leads to abusive behavior.

Constraints:

- Negative dispositions and high irritability may only express as:
  - reduced frequency of optional engagement
  - shorter, more neutral replies
  - stronger boundary-setting (e.g. suggesting other channels, docs, or slowing a conversation)
- They must **not** express as:
  - insults, sarcasm that targets the user personally
  - harassment, mockery, or targeted pile-on behavior

Safety enforcement:

- PolicyService must gate all style decisions and content proposals:
  - run outputs through style/safety filters
  - treat high-irritability intentions as candidates for *extra* scrutiny, not looser behavior
- CriticService should have explicit "de-escalation" heuristics:
  - if many negative signals accumulate in a short time:
    - prefer cooling off
    - suggest breaks or channel changes
    - downgrade proactive engagement in that context

### 12.5 Integration with sleep and greeting behavior

The existing notions of sleep, quiet hours, and greeting map naturally onto mood and relationships:

- sleep schedule:
  - primarily drives very low `energy` and `sociability`
  - encourages minimal or no interaction except for urgent, high-priority cases (e.g. forced wake)

- morning greeting:
  - can be framed as an intention that:
    - boosts `energy` and `sociability` when executed successfully
    - warms up relationships in targeted channels

- quiet outreach:
  - intensity and frequency can be modulated by:
    - global `energy` and `sociability`
    - avoidance of channels and users where relationships are strained

This allows the cognitive state to feel continuous: the agent doesn’t just "flip flags" for sleep or greeting—it shifts its internal affect in ways that influence broader behavior.

## 13. Memory and Relationship Design

### 11.1 Channel memory

Preserve the strong self-contained local memory model:

- recent transcript persistence
- searchable older messages
- pinned memory

But improve the retrieval interface:

- move from loose helper calls to an explicit `MemoryService`
- return structured memory snippets with provenance and scores

### 11.2 User profiling

Preserve the current idea of:

- one profile per Discord user per bot account
- facts
- summaries
- dispositions
- reply modulation

But integrate it directly into the world model:

- profiles are not a side subsystem
- they are part of how users are represented overall

### 11.3 Distillation

Retain background distillation but formalize it as a service:

- enqueue candidate profile buffers
- distill asynchronously
- update profile summaries/facts/dispositions
- log confidence and provenance

This should remain optional and configurable due to cost.

## 14. Proactive Behavior

The current plugin already contains proactive behavior. The new plugin should preserve it, but make it intentional and goal-driven rather than mostly cron-driven.

### 12.1 Behaviors to preserve

- morning greetings
- quiet-channel outreach
- sleep/goodnight behavior
- forced wake
- delayed wake replies
- presence cycling

### 12.2 New framing

These should be interpreted as intention types:

- greeting is a scheduled social action in service of "maintain presence"
- outreach is an engagement intention
- sleep is a policy mode plus a presence state
- forced wake is a high-priority interruption rule

### 12.3 Future proactive behaviors

Once the world model exists, the plugin can also support:

- follow-up on prior promises
- recap after important discussions
- "you mentioned X yesterday, how did it go?"
- summarize hot channels after meetings
- detect repeated confusion and offer docs/help

These should not be built immediately unless needed, but the architecture should make them natural.

## 15. Presence and Social Embodiment

Presence is not cosmetic in this design. It is part of the bot's social state.

The system should support:

- awake state
- quiet-hours state
- sleep state
- forced-wake temporary state
- optional LLM-generated short statuses

Presence should be derived from world/policy state rather than polled as a disconnected subsystem.

For example:

- if asleep, use sleep presence
- if quiet hours, idle presence
- if highly active and social, normal awake cycling
- if a specific social mode is active, reflect that in status selection

## 16. Safety, Policy, and Permissions

Policy must become a first-class service, not scattered checks.

### 14.1 Policy responsibilities

- Discord permission validation
- user allowlist/denylist logic
- bot-ignore behavior
- reaction and emoji allowlist enforcement
- quiet-hours constraints
- sleep constraints
- per-channel reply modes
- rate limiting
- proactive frequency caps
- anti-spam and anti-repeat checks
- media-source safety and content policy
- meme appropriateness policy
- voice participation permissions and per-guild opt-in rules

### 14.2 Safety principle

The system must be able to think about many possible actions, but it should only execute those that pass policy.

This means:

- deliberation can over-generate
- policy narrows
- execution obeys

## 17. Storage Architecture

Use SQLite again for the rewrite, but move to a clearer schema and repository pattern.

### 15.1 Why SQLite remains appropriate

- local
- simple
- self-contained
- proven by the current plugin
- enough for the expected scale of a single Sapphire plugin

### 15.2 Storage categories

The new database should include:

- accounts
- guilds
- channels
- users
- messages
- memories
- profiles
- profile facts
- profile buffers
- observations
- tasks
- traces
- llm debug logs
- presence state
- sleep state
- outreach/greeting logs
- media artifacts
- voice sessions
- voice transcripts
- voice summaries

### 15.3 Repository split

- `AccountRepository`
- `ChannelRepository`
- `MessageRepository`
- `MemoryRepository`
- `ProfileRepository`
- `TaskRepository`
- `TraceRepository`
- `PresenceRepository`
- `MediaRepository`
- `VoiceSessionRepository`

Each service should depend on repositories, not raw SQL helpers spread across the codebase.

## 18. Plugin Boundaries and Sapphire Integration

The new plugin should integrate with Sapphire, but it should do so through explicit boundaries.

### 16.1 Keep

- plugin manifest registration
- daemon lifecycle integration
- route registration
- settings UI
- schedule/task/event integration
- Sapphire LLM/provider/tool execution for conversational replies

### 16.2 Improve

- avoid monkey-patch-first design where possible
- detect capabilities explicitly
- isolate Sapphire-specific assumptions behind adapter interfaces

### 16.3 Recommended Sapphire boundary modules

- `SapphireEventBridge`
- `SapphireLlmBridge`
- `SapphireSettingsBridge`
- `SapphireSchedulerBridge`
- `SapphireSpeechBridge` (optional/future)

These adapters reduce leakage of Sapphire internals into the rest of the plugin.

## 19. Proposed Folder Structure

```text
plugins/discord_cognitive/
├── plugin.json
├── daemon.py
├── runtime/
│   ├── container.py
│   ├── lifecycle.py
│   ├── scheduler_loop.py
│   └── health.py
├── transport/
│   ├── discord_transport.py
│   ├── discord_event_adapter.py
│   ├── discord_presence.py
│   ├── discord_commands.py
│   └── voice_transport.py
├── cognition/
│   ├── world_model.py
│   ├── observation_interpreter.py
│   ├── attention_service.py
│   ├── goal_engine.py
│   ├── intent_engine.py
│   ├── critic_service.py
│   └── policy_service.py
├── conversation/
│   ├── batching_service.py
│   ├── conversation_service.py
│   ├── prompt_context_service.py
│   ├── reply_style_service.py
│   ├── reaction_service.py
│   ├── gif_service.py
│   ├── media_service.py
│   └── meme_service.py
├── memory/
│   ├── memory_service.py
│   ├── profile_service.py
│   ├── profile_distill_service.py
│   └── task_service.py
├── voice/
│   ├── voice_service.py
│   ├── voice_perception_service.py
│   ├── voice_execution_service.py
│   ├── voice_session_service.py
│   └── voice_turn_taking_service.py
├── storage/
│   ├── sqlite.py
│   ├── migrations.py
│   ├── repositories/
│   │   ├── accounts.py
│   │   ├── channels.py
│   │   ├── messages.py
│   │   ├── memory.py
│   │   ├── media.py
│   │   ├── profiles.py
│   │   ├── tasks.py
│   │   ├── traces.py
│   │   ├── presence.py
│   │   └── voice_sessions.py
├── api/
│   ├── accounts.py
│   ├── settings.py
│   ├── profiles.py
│   └── traces.py
├── models/
│   ├── settings.py
│   ├── world.py
│   ├── observations.py
│   ├── intentions.py
│   ├── profiles.py
│   ├── media.py
│   └── voice.py
├── sapphire/
│   ├── event_bridge.py
│   ├── llm_bridge.py
│   ├── scheduler_bridge.py
│   ├── settings_bridge.py
│   └── speech_bridge.py
├── web/
│   └── index.js
├── statuses/
│   ├── awake.json
│   └── sleep.json
└── docs/
    └── architecture.md
```

## 20. Feature Compatibility Requirements

The rewrite should preserve these externally visible capabilities unless intentionally deprecated later.

### Must preserve

- multi-account support
- Discord event ingestion
- event-to-Sapphire conversational reply path
- per-channel batching
- user- and channel-aware memory
- per-user profile memory
- slash commands:
  - `/ask`
  - `/summarize`
  - `/remember`
  - `/forget-me`
- humanized reply timing
- reactions
- GIF follow-ups
- image understanding support
- inbound GIF understanding
- meme-aware reply context
- quiet hours
- sleep/goodnight
- forced wake
- wake-time delayed replies
- presence cycling
- settings API and admin UI
- traces and debug logging

### Should improve

- long-horizon planning
- follow-up awareness
- intention prioritization
- operator understanding of why proactive behavior happened
- privacy and retention controls
- structured testing and component isolation
- explicit media/meme handling
- clean path to future real-time voice support

### Ambitious new capabilities

These are not required for first parity, but the architecture should deliberately make room for them:

- sending memes as a distinct behavior
- media-aware intentions such as `respond_to_media`
- voice-session awareness
- joining Discord voice channels
- transcription-backed voice observations
- optional real-time spoken participation

## 21. Migration Strategy

Because the new plugin is a separate project, migration should be treated as an adoption path, not an in-place rewrite.

### 19.1 New plugin identity

Use a new plugin name and folder, not `leona_discord`.

This avoids:

- accidental coupling to the old plugin
- migration confusion
- operator fear of breaking an existing working bot

### 19.2 Migration phases

1. Build new plugin with parity for core Discord reply flow.
2. Add memory/profile/proactive parity.
3. Add world-model and intention loop.
4. Allow side-by-side evaluation in separate environments if desired.
5. Only later consider migration/import tooling for selected old data.

### 19.3 Data migration

Import from `leona_discord` should be optional.

Possible imported data:

- recent channel history
- pinned memories
- profiles and facts
- selected settings

Do **not** assume schema compatibility. Build explicit import tooling if needed.

## 22. Testing Strategy

The rewrite should be more testable than the current plugin.

### 20.1 Unit tests

Test pure logic for:

- settings layering
- attention scoring
- intent scoring
- batching behavior
- policy decisions
- prompt-context assembly
- profile updates
- media classification and meme policy
- voice turn-taking and voice-intention scoring

### 20.2 Service tests

Test:

- conversation flow from observation to intention
- proactive task generation
- sleep and forced-wake transitions
- presence selection
- repository behavior
- media pipeline behavior
- voice session state transitions

### 20.3 Integration tests

Test:

- Discord event -> observation -> intention -> execution
- slash command flows
- Sapphire event bridge behavior
- no-double-send guarantees
- restart/recovery behavior
- image/GIF/meme reply flow
- voice join/listen/leave lifecycle

### 20.4 Observability tests

Ensure:

- traces are recorded
- LLM debug artifacts are captured when enabled
- proactive actions have explainable reasons
- media and voice decisions have traceable reasons

## 23. Risks and Trade-Offs

### 21.1 Main risks

- The world-model design is more complex than a reactive bot.
- Continuous reasoning can become expensive if attention filtering is weak.
- Poorly bounded intentions can lead to excessive proactivity.
- Rich profile/memory systems increase privacy obligations.
- Overly abstract architecture can slow delivery if not scoped carefully.
- Real-time voice substantially increases complexity, latency sensitivity, and operational risk.
- Meme support can create moderation, appropriateness, and brand/persona risks if not strongly policy-gated.

### 21.2 Main trade-offs

- More architecture up front in exchange for long-term extensibility.
- More explicit state modeling in exchange for lower accidental complexity.
- More disciplined service boundaries in exchange for a slower initial build.
- Voice support should be phased in carefully to avoid overwhelming the first implementation.

## 24. Final Recommendation

The correct path is **not** to continue extending `plugins/leona_discord` until it resembles a cognitive agent.

The correct path is to build a new plugin with:

- a new identity
- a world-model-first architecture
- clear separation of perception, deliberation, and execution
- explicit service boundaries
- intentional preservation of the strongest current Discord behaviors
- explicit first-class media handling
- an intentional architectural seam for future real-time voice participation

In short:

> Do not build a better reactive Discord bot.
>
> Build a persistent cognitive system whose first environment is Discord.
