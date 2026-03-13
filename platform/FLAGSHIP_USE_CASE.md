# Flagship Use Case: Backstage

`Backstage` should be the flagship example for `mozaiks core`.

It is a comedy club operating system.

The user does not just "chat with an AI comedian." They enter a live backstage environment where Mozaiks helps turn a rough life story, awkward confession, strange trait, or pitch into a structured comedy set.

This makes the platform feel:

- playful
- cinematic
- interactive
- memorable

But it still proves the real architecture:

- global workflow orchestration
- workflow-level MFJ
- inline UI tools
- artifact UI tools
- persistent modules
- event-driven state changes
- human checkpoints

## Why This Is The Right Showcase

The current example proves mechanics but not imagination.

`Backstage` is better because it feels like a real next-gen app:

- chat is the front door
- the app transforms around the user
- parallel specialists collaborate behind the scenes
- the user gets both a live conversation and durable club surfaces

It is entertaining enough to demo, but structured enough to explain cleanly.

## The Product

`Backstage` helps someone create:

- a roast set
- a crowd-work set
- a clean observational set
- a character bit
- a weird comedy persona

It can also be used by:

- comedy clubs
- creators
- podcasters
- live event hosts
- talent managers

Example inputs:

- "Roast me based on how I describe myself."
- "Turn my awful dating story into a set."
- "Help me make a 3-minute clean set about startup life."
- "I want a character who sounds like a failed motivational speaker."

## User Experience

The user experiences one continuous show.

They do not think in workflows.

### Visible journey

1. User arrives in chat and shares a story, trait, or premise.
2. `GreenRoom` host asks 2-4 clarifying questions.
3. An inline `PremisePulseCard` appears with:
   - set type
   - tone
   - risk level
   - strongest angles
4. User approves the direction or adjusts boundaries.
5. The artifact panel opens a live `SetBoard`.
6. `WritersRoom` runs parallel comedy lanes behind the scenes.
7. Inline lane updates appear in chat as each lane finishes.
8. The artifact board fills with jokes, angles, tags, and crowd hooks.
9. User chooses a direction or asks for a rewrite.
10. `MainStage` presents the final polished set.
11. Persistent modules store the set, top bits, and club history.

The experience should feel like stepping backstage before a live show.

## Workflow Architecture

This flagship should use three workflows.

## 1. `GreenRoom`

Purpose:

- welcome the user
- understand the premise
- ask about tone, boundaries, and intent
- produce the canonical comedy brief

Key agents:

- `ClubHostAgent`
- `InterviewAgent`
- `PremiseCanonAgent`

Outputs:

- `SetBrief`
- tone
- boundary rules
- target audience
- best raw premise

UI:

- inline `PremisePulseCard`
- inline `BoundaryBar`

This workflow is the warm, conversational front door.

## 2. `WritersRoom`

Purpose:

- decompose the brief into comedy angles
- fan out parallel writing lanes
- fan back in
- present one coherent set board

Key agents:

- `WritersHostAgent`
- `DecompositionAgent`
- `RoastLaneAgent`
- `ObservationalLaneAgent`
- `AbsurdistLaneAgent`
- `CrowdWorkLaneAgent`
- `HeadWriterAgent`

MFJ behavior:

- `DecompositionAgent` emits lane specs
- runtime fans out child runs in parallel
- each child run starts a specialist comedy lane
- fan-in merges results into `writers_room_results`
- parent resumes at `HeadWriterAgent`

Outputs:

- strongest jokes
- alternative set directions
- crowd hooks
- tags and closers
- weak points / over-the-line warnings

UI:

- inline `LaneTicker`
- inline `DirectionChooser`
- artifact `SetBoard`

This is the core Mozaiks showcase workflow.

## 3. `MainStage`

Purpose:

- turn the chosen material into a final performable set
- package the final routine
- persist the performance artifacts

Key agents:

- `StageHostAgent`
- `SetPolishAgent`
- `CloserAgent`

Outputs:

- final set
- opening line
- middle structure
- closer
- alternate clean version

UI:

- artifact `FinalSetCard`

This is the payoff.

## Global Journey

The global pack should be simple:

- `GreenRoom`
- `WritersRoom`
- `MainStage`

That is enough to show universal orchestration clearly.

## MFJ Design

The workflow-level MFJ lives inside `WritersRoom`.

The mental model is:

- user agrees on the premise
- decomposition agent defines the conveyor belt
- runtime fans out parallel joke lanes
- runtime fans in the results
- head writer synthesizes the final set direction

Suggested first-pass lanes:

- `Roast`
- `Observational`
- `Absurdist`
- `CrowdWork`

Optional later lanes:

- `Character`
- `CleanSet`
- `DarkHumor`
- `CorporateFriendly`

## UI Tool Strategy

This example should explicitly use both inline and artifact modes.

## Inline UI

Use inline components for fast interaction:

- `PremisePulseCard`
- `BoundaryBar`
- `LaneTicker`
- `DirectionChooser`
- `ApproveSetBar`

Inline components should feel live and playful.

## Artifact UI

Use artifact components for richer surfaces:

- `SetBoard`
- `JokeMap`
- `FinalSetCard`
- `ShowPacket`

Artifact mode is where the user can inspect, compare, and revisit the material.

## Modules

This flagship should also ship persistent module pages.

## `lineup_board`

Shows:

- active performers
- current set status
- room progress
- show queue

## `bit_vault`

Shows:

- saved jokes
- rejected jokes
- winning closers
- reusable tags

## `crowd_scoreboard`

Shows:

- favorite bits
- strongest reactions
- crowd-safe vs risky material
- set rankings

## `show_archive`

Shows:

- completed sets
- performer history
- versions
- final show packets

These pages prove that Mozaiks is not only a chat experience.

## Event System Fit

`Backstage` is also a strong event-system showcase.

Examples:

- `set.created`
- `set.brief_confirmed`
- `writers_room.started`
- `writers_room.wave_started`
- `writers_room.wave_completed`
- `set.direction_selected`
- `set.finalized`
- `show.archived`

Consumers:

- notification handlers
- module updaters
- artifact refresh handlers
- future subscription or club-management listeners

This shows how the same event system could be reused for other app types later.

## Why This Is Better Than The Current Example

The current example is still a demo.

`Backstage` is better because it:

- is instantly more fun
- still maps cleanly to Mozaiks primitives
- gives a natural reason for MFJ
- gives a natural reason for inline and artifact UI
- gives a natural reason for persistent modules
- feels more like a real product someone would remember

## Recommended Platform Structure

```text
platform/
  workflows/
    _pack/
      workflow_graph.json
    GreenRoom/
    WritersRoom/
    MainStage/
  modules/
    lineup_board/
    bit_vault/
    crowd_scoreboard/
    show_archive/
```

`admin_portal` can remain as a platform utility module.

## Recommendation

If you want a flagship example that is memorable, flashy, and still architecturally honest, `Backstage` is the right replacement.

It showcases:

- chat-led intake
- global workflow routing
- workflow-level MFJ
- inline UI tools
- artifact UI tools
- persistent modules
- event-driven updates
- a user experience that feels alive

This should replace the current platform example before we build the first-party `mozaiks.ai` product.
