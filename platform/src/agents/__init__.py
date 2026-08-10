"""Agents package — one module per agent.

Each agent has a clear input contract, its own system prompt, its own
fallback handling, and is independently testable.

QueryPlannerAgent      query_planner.py        query → QueryPlan
CodeQAAgent            code_qa_agent.py        query + context + history → QAResponse
FileSummaryAgent       file_summary_agent.py   file_path + source + entities → str
FolderSummaryAgent     folder_summary_agent.py folder + file_summaries → str
RepoSummaryAgent       repo_summary_agent.py   repo_name + folder_summaries + intent → str
CitationCorrectionAgent citation_correction_agent.py  answer + report → CorrectionResult
"""
