# How the Factory builds your app

In Studio, choose **Create App** in the workspace header and describe who will use the app and what they should be able to do. During the build, open the app’s **Project** page to see the current step or continue the conversation. Open **Documents → Design** to read a saved design and its versions. The **App** card opens build review. Several agents can contribute to one document. [Studio Project](../studio/app-dashboard.md#project)

For example, ask for “a field journal where anyone can read published entries and only editors can archive them.” That brief identifies what a record is, who can see it, and who may change it.

## Follow a new app through Studio

| Step | What shapes the app | What you can inspect or decide |
| --- | --- | --- |
| 1. Idea · `ValueEngine` | `ValueInterviewAgent` clarifies the audience and the problem. `ResearchAgent` looks for alternatives and gaps. `GapAnalysisAgent` turns that evidence and your answers into the concept, including proposed features and constraints. | Review **Idea & value**. If the journal is for museum curators, say so before choosing **Approve Concept** or **Request changes**. The accepted brief guides later decisions. |
| 2. Look & feel · `ThemeCapture` | `ThemeInterviewAgent` collects visual direction. `ThemeAnalysisAgent` translates it into design tokens. `ThemeConfigAssemblerAgent` produces the theme configuration consumed by the app. | Read **Look & feel**. Describe the typography, palette, or example you want the screens to reflect. Then choose **Build it with me** for more checkpoints or **Build it for me** for fewer. |
| 3. Design · `DesignDocs` | `DesignPackSelectionAgent` proposes pack IDs, including an empty selection. The Factory verifies the IDs, dependencies, versions and content digests. `DesignDocsAgent` uses the selected contracts to describe pages, modules, records, access rules and any AI workflows. | Read **Design**. Check the reader and editor experiences, the actions each role receives, and the selected packs when prompted for review. A public reading page and the right to archive entries are separate decisions. |
| 4. Plans & pricing · `SubscriptionContractDesigner` | `ContractDesignerAgent` describes subscription features, limits and metering when the design calls for them. It can also record that subscriptions are unnecessary. | Read **Plans & pricing**. A paid tier needs a clear rule for what it unlocks. A free journal can proceed without one. |
| 5. AI workflows · `AgentGenerator` | This workflow turns the AI processes in the saved design into agents, tools, outputs and routing that will run **inside your app**. It can also record that the app needs no AI workflows. | Read **AI workflows** and review the proposed flow when presented. Saving an entry can be an ordinary module action. Asking an AI to draft a summary for an editor may justify an app workflow. |
| 6. App · `AppGenerator` | The planner assigns files and dependencies. Page, data and backend workers fill in app-specific behavior. Assembly combines their accepted outputs with the selected packs and stages the app bundle. | Read **App**. Inspect which pages and actions the plan includes. The next two sections explain who owns each part. |
| 7. Page images · `AppMediaGenerator` | `MediaPlanAgent` turns planned image slots into jobs. `SquareImageMakerAgent`, `LandscapeImageMakerAgent` and `PortraitImageMakerAgent` handle their respective shapes. `MediaPublishAgent` records the result in a draft bundle version when images are published. | Inspect the resulting imagery and any missing assets or placeholders. Image work depends on the app’s asset plan. |
| 8. Security findings · `SecurityReadiness` | `SecurityReadinessAgent` inspects the generated bundle and records advisory findings. Separate validation checks determine whether the build can advance. | Review findings alongside the validation results, especially for public pages, protected actions and integrations. |
| 9. Draft review · `AppReview` | `ReviewAgent` presents the saved build and submits requested changes. Studio records your decision against a specific version. | Review the files, preview and available check results. Ask for a correction or accept a passing draft. Activating the accepted version puts it in the workspace. Deployment is a separate step. |

## Who builds the AI workflows?

AG2 runs the Factory’s agents and their tools. Within `AgentGenerator`, those agents design a second set of agents only if your app needs them. Their deliverable is a workflow definition for the generated workspace’s `workflows/` directory, beside `app/`.

| Agents | Their part |
| --- | --- |
| `InterviewAgent` and `PatternAgent` | The interviewer clarifies missing automation requirements. The pattern agent follows the approved workflow scope in the design and chooses an AG2 execution pattern for each planned workflow. |
| `ProjectOverviewAgent` and `PackBuildCoordinator` | The first presents the proposed workflow diagram for review. For an approved, nonempty plan, the coordinator starts a separate build task for each workflow. |
| `WorkflowBundleBuilderAgent` | Each worker receives one workflow brief. It produces the declarations for its agents, tools, typed outputs and handoffs, with Python or React where that workflow requires it. |
| `WorkflowBundleMergeAgent`, `PackMetadataAgent` and `DownloadAgent` | The merge agent requests a deterministic merge after a repair. The metadata agent describes routing and dependencies across the generated workflows. The download agent requests checks and export of the workflow bundle. |

## Who builds the pages, data and backend?

Within `AppGenerator`, the planner turns the saved design (`DesignDocsBundle`) into a build plan (`AppBuildPlanOutput`). Each task names its output paths and dependencies, so a page can bind to an action its module already declares. The roster also includes specialist checks and repair roles. The approved plan determines which workers a particular build needs.

| Agents | What they shape |
| --- | --- |
| `InterviewAgent` and `AppPlanAgent` | The interviewer resolves missing implementation choices when needed. Autonomous participation can go straight to the planner. The planner produces the build plan, including the pages, capabilities, file owners and task order. |
| `ConfigMiddlewareAgent` | Declares modules and their actions, permissions and supporting contracts. It also sets up app-owned service integrations when the plan calls for them. |
| `DatabaseAgent` and `ModelAgent` | The database worker prepares the app’s data contract and migrations. The model worker supplies module-local request, response and record types where the data contract leaves work for it. |
| `ServiceAgent` and `ControllerAgent` | The service worker implements app-specific business behavior behind module actions. The controller worker handles the planned API surface, such as a declared integration callback. |
| `AppSchemaAgent`, `FrontendStubAgent` and `AdminRegistryAgent` | The page worker emits the app manifest and typed page sections, or an approved custom React route. The frontend stub worker handles bounded customization or repairs. The admin registry worker connects declared admin panels to their pages. |
| `RefinementHarnessAgent` | Produces an app-local refinement configuration when the app itself needs one. The Factory also has its own harness for managing changes to a built app. |
| `ModuleContractQualityAgent`, `ModuleRuntimeQualityAgent` and `AppUIQualityAgent` | Invoke checks on module declarations, backend implementations and page artifacts respectively. A failed check can direct a correction back to the responsible worker. |
| `AssemblyAgent` and `IntegrationReadinessAgent` | The assembly agent requests the deterministic merge of pack files and admitted worker outputs. The readiness agent checks connector requirements and identifies setup the app still needs. |
| `AppValidationAgent` and `DownloadAgent` | The validation agent requests the build checks and can route a bounded repair. The download agent prepares the validated app bundle for export when the gates allow it. |

## How one feature becomes files

For the field journal, suppose the design selects the **Content** pack. Its registered templates supply the `content` module and the data contract for `entries`. Creating, updating and archiving entries share the `content.manage` permission. During design review, approve those actions together for the editor role and check that readers can only see published entries. The app owns its pages, so the same pack can support a curator’s catalog or a recipe journal with different layouts and branding. [Content pack ownership](../guides/content-app-starters.md)

1. **The worker describes the page.** For a declarative editor, `AppSchemaAgent` returns an `AppSchemaOutput` naming shipped UI components and their action bindings. A table can show entries and a form can edit one. An Archive control needs the module `content`, the action `archive_entry`, and the selected record’s `entry_id`.
2. **Tools write and check the files.** The page compiler resolves the references and writes page YAML under `app/ui/pages/`. App identity belongs in `app/app.json`. Assembly reads registered templates from disk. A fixed file can be copied directly. A declared Jinja template can fill in build-specific values, with the resulting YAML or JSON parsed before the file is accepted. [Pack selection and assembly](../guides/build-context-selection.md)
3. **The running app uses the shared UI library.** The platform serves the validated page schema as JSON. The browser renders its registered components using the app’s theme. An approved custom route can add React for an interaction the library cannot express. The module enforces permissions when an action runs. [Adding a page](../guides/adding-pages/01-overview.md)

At review, try the journal as a reader and as an editor. Can the reader see a published entry? Can the editor archive that entry and see the change after reloading? Does the app refuse the same write from a reader? These checks exercise the page, its data binding and the access rule together.

This division also affects cost. A worker receives the contracts, task inputs and relevant dependency outputs it needs. Deterministic assembly can reuse an implementation without asking a model to write those files again. Models still consume tokens for prompts, structured-output schemas, responses and any repair turns. Open the app’s **Token Usage** page to inspect recorded tokens by workflow and chat and see estimated costs. [App dashboard](../studio/app-dashboard.md)

## What changes when you return?

**Starting with an existing repository.** Choose the existing-app path to enter `ExistingAppDiscovery`. `DiscoveryHostAgent` identifies the app from its source. `CapabilityMapperAgent` inventories what it does. `IntegrationPlannerAgent` proposes how Mozaiks can fit in, and `ModuleDecomposerAgent` maps existing behavior to modules and workflows on paths that need that detail. `DiscoveryArtifactAssemblerAgent` saves the findings and open questions. Discovery ends with an adoption plan. Generating an overlay or modules is a separate journey. [Existing app build targets](../studio/existing-app-factory-target.md)

**Changing an app you have already built.** From **Project → Make a change**, name the outcome and what should stay in scope. “Rename the editor heading” gives the refinement harness a different job from “let guest curators archive entries.” It reads the current artifacts and chooses a scope. The dependency graph records which saved outputs depend on an earlier decision. A heading edit can reuse the existing module. A change to the access policy can require the design and the app that consumes it to be checked again. [Genesis Builds and Refinement Runs](genesis-builds-and-refinement-runs.md)

**Changing the logo.** A logo-only revision uses `BrandAssetGeneratorWorkflow`. Its `BrandDesignerAgent` offers artwork based on the app’s brand and your request. You choose which option to save.

For contributors tracing a specific build, the Factory registry declares the journeys and each workflow folder contains its roster, output models, tools and transitions. [Factory sequence registry](https://github.com/BlocUnited-LLC/mozaiks/blob/main/factory_app/workflows/extended_orchestration/extension_registry.json) · [Workflow definitions](https://github.com/BlocUnited-LLC/mozaiks/tree/main/factory_app/workflows)
