# Evidence checking and resumable model workflows

[中文](workflow_reliability.zh.md) · [Workflows](workflows.en.md) · [Patient design](simulated_patients.en.md)

Complex generation and review tasks now use separate stages with explicit output checks. The stages use the configured LLM; they do not require another provider, dependency or service port. LLM and embedding settings remain independent.

## Patient and course design

In the simulation workspace, select Instructor or Researcher, enter a training theme and optionally supply patient facts. Generation runs in the background: plan → dossier → compiled scenario → review → optional focused repair → fresh review. The form shows progress, saved jobs and a resume action. Successful stages survive a retry.

The reviewer sees the same pinned user facts and compiled public fields that are saved. Missing reviews, contradictory pass results and empty repairs remain unresolved. Editing or copying a case invalidates its model review. Review the current version before approval, or explicitly record the reason for a human review override. Structural failures cannot be overridden.

Course batches first assign distinct learning objectives and experiences to cases. The final review checks the actual saved dossiers against that plan, including repeated patient facts and unsupported coverage. The course panel displays the plan and coverage report. After editing cases, recheck the existing batch; already saved cases are reused. A coverage result that needs review does not prevent saving drafts.

## Knowledge extraction

Confirm the fields and evidence requirements, run a sample, inspect its actual output, and explicitly accept it before starting a full run. To skip sampling, select that option and provide a reason. The server stores the decision and checks that an accepted sample still matches the source, schema and settings. Editing the form invalidates the previous confirmation.

Schema validation checks nested objects, required fields, array items, types and supported value/length constraints. Unsupported schema constraints are reported rather than ignored. Every provided quotation is checked separately against the original source, with character locations; a valid quote cannot hide another fabricated quote.

The default semantic-review mode reviews extracted claims and other generated content. Pure quotation extraction can use the shorter path; **Always** requests semantic review even for that case. The semantic stage checks whether the evidence supports the output, requests at most one focused repair, and reviews that repair. Rejected results remain in failure records with their drafts and issues, separate from accepted candidates.

Recovery is per source record. An interrupted unfinished record can repeat its internal model stages; completed records are retained. Sample selection spans the source rather than taking only the beginning.

## Transcript analysis and session debriefs

Model evaluation and packaging in the transcript workspace create background jobs. Refreshing restores the input, job and progress. Retry a failed or interrupted job to reuse completed windows and tool results. Packaging reuses the matching review run. Changed text, roles, language, model, skills or knowledge invalidate reuse.

Review skills execute their declared context and knowledge-search tools. Findings need valid turn IDs and exact source quotations; knowledge citations must come from that run's retrieval. Failed evidence checks move content into a separate section, outside the verified synthesis. Cross-window themes cite verified findings. The interface shows coverage, quotations, retrieved references and unresolved items.

Simulation debriefs process the complete transcript in bounded segments, retaining actual turn IDs and every source character. They preserve early findings as well as late ones, validate quotations and expose coverage. Successful segments are cached for retry. Model interpretation remains a reviewable judgement even when its quotation is valid.

## Expert work and Skill improvement

Expert response review separates source evidence, six review dimensions, recommendations and independent checks. Teaching dialogues use a plan, a draft, review and bounded repair with re-review. These jobs reuse successful stages on retry and produce readable reports alongside structured results. The requested dialogue length is a count of individual utterances.

Response comparisons receive actual retrieved knowledge text. Candidate citations are checked against the supplied IDs and quotations. Missing knowledge is recorded as limited evidence; historical session IDs are not attached as if they supported new candidates.

Skill improvement uses the selected Skill, its references, input text and failure evidence to diagnose a problem before proposing focused edits. Stage checkpoints preserve completed diagnosis. The existing behavior comparison and human-reviewed installation gate still govern replacing a Skill; producing a draft does not install it.

The support assistant summarizes messages as they leave its recent context window and persists the summarized-message position. Short-message conversations therefore retain earlier context without repeatedly summarizing all history.

## Stored state and current limits

Back up the existing application data directory. New state includes patient/course stage checkpoints, transcript review runs/jobs, extraction failure traces and expert generation checkpoints. These contain source or synthetic case content and stay with local application data, outside the Git source release.

Transcript jobs appear in the transcript page's recent-job list; they are not yet aggregated into the global Tasks Center. Expert intermediate checkpoints support retry, while its page shows the completed stage trace rather than live stage progress. Course coverage currently uses one bounded whole-batch review; exceeding its context budget leaves it needing review. This upgrade does not add an independent semantic verifier to every real-time patient utterance, redesign complex query understanding, or automate acceptance of Skill behavior evaluations.
