"""Who may call each operation: a golden file, edited by hand.

`test_access_matrix.py` rebuilds this table from the operation registry and fails on any
difference, so a new operation or an access change shows in review. It also checks that the
operation pipeline gives each kind of caller what the table says.

- access: `public` (anyone), `signed_in` (any account), or the permission the caller's roles
  must give (`accounts.manage`: the admin role).
- http: the route's method; `-` when only server code calls the operation.
- tool: `read` for the agent's read tools; a tool that changes something shows its effect
  (`data`, `external`), which an MCP caller such as the agent may use only once a person
  approves the call or under a notebook grant; `none` for `ask_agent`, a tool the agent itself
  never gets; `-` for an operation that is no tool.
- workspace, mcp: `yes` when a personal token of that kind may call it: the read tools, and
  also `accounts.me` for a workspace token or `ask_agent` for an MCP token. A workspace token
  signs in at /api and an MCP token at /mcp; there, a row without `yes` answers 403.
"""

MATRIX = """
operation                               access           http    tool      workspace  mcp
accounts.add                            accounts.manage  -       -         -          -
accounts.approve                        accounts.manage  -       -         -          -
accounts.auth_callback                  public           GET     -         -          -
accounts.auth_login                     public           GET     -         -          -
accounts.auth_logout                    signed_in        POST    -         -          -
accounts.change_me                      signed_in        PATCH   -         -          -
accounts.create_token                   signed_in        POST    -         -          -
accounts.create_workspace_token         signed_in        POST    -         -          -
accounts.disable                        accounts.manage  -       -         -          -
accounts.end_sessions                   accounts.manage  -       -         -          -
accounts.grant                          accounts.manage  -       -         -          -
accounts.list_tokens                    signed_in        GET     -         -          -
accounts.list_users                     accounts.manage  -       -         -          -
accounts.list_workspace_tokens          signed_in        GET     -         -          -
accounts.me                             signed_in        GET     -         yes        -
accounts.promote                        accounts.manage  -       -         -          -
accounts.revoke                         accounts.manage  -       -         -          -
accounts.revoke_token                   signed_in        DELETE  -         -          -
accounts.revoke_workspace_token         signed_in        DELETE  -         -          -
accounts.sign_out                       signed_in        DELETE  -         -          -
accounts.workspace_token_from_hub       public           POST    -         -          -
agent.agent_run                         signed_in        -       -         -          -
agent.ask_agent                         signed_in        POST    none      -          yes
agent.cancel_run                        signed_in        POST    -         -          -
agent.embed                             public           -       -         -          -
agent.get_answer                        signed_in        GET     read      yes        yes
agent.get_run_recovery                  signed_in        GET     -         -          -
agent.interrupt_run                     public           -       -         -          -
agent.issue_run_token                   signed_in        -       -         -          -
agent.list_tools                        signed_in        GET     -         -          -
agent.prune_run_events                  public           -       -         -          -
agent.resolve_run_token                 public           -       -         -          -
agent.respond                           public           -       -         -          -
agent.revoke_run_token                  signed_in        -       -         -          -
agent.run_agent                         signed_in        POST    -         -          -
agent.run_events                        signed_in        GET     -         -          -
approvals.decide_approval               signed_in        POST    -         -          -
approvals.list_approvals                signed_in        GET     -         -          -
approvals.list_notebook_grants          signed_in        GET     -         -          -
approvals.read_approval                 signed_in        GET     -         -          -
approvals.request_approval              signed_in        -       -         -          -
approvals.revoke_notebook_grant         signed_in        DELETE  -         -          -
conversations.append_for_run            signed_in        -       -         -          -
conversations.create_conversation       signed_in        POST    -         -          -
conversations.delete_file               signed_in        DELETE  -         -          -
conversations.file_content              signed_in        GET     -         -          -
conversations.list_conversations        signed_in        GET     -         -          -
conversations.list_files                signed_in        GET     -         -          -
conversations.load_for_run              signed_in        -       -         -          -
conversations.read_conversation         signed_in        GET     -         -          -
conversations.store_generated_file      signed_in        -       -         -          -
conversations.update_conversation       signed_in        PATCH   -         -          -
conversations.upload_file               signed_in        POST    -         -          -
cube.compare_missions                   signed_in        POST    read      yes        yes
cube.explain_similarity                 signed_in        POST    read      yes        yes
cube.find_missions                      signed_in        POST    read      yes        yes
cube.get_mission                        signed_in        GET     read      yes        yes
cube.get_value_evidence                 signed_in        GET     read      yes        yes
cube.label_values                       signed_in        -       -         -          -
cube.list_missions                      signed_in        GET     read      yes        yes
cube.list_parameters                    signed_in        GET     read      yes        yes
cube.prepare_values                     signed_in        POST    read      yes        yes
cube.similar_missions                   signed_in        POST    read      yes        yes
cube.submit_values                      signed_in        POST    data      -          -
health.live                             public           GET     -         -          -
health.ready                            public           GET     -         -          -
jobs.cancel_job                         signed_in        POST    -         -          -
jobs.list_jobs                          signed_in        GET     -         -          -
jobs.read_job                           signed_in        GET     -         -          -
library.check_citation                  signed_in        POST    read      yes        yes
library.compare_versions                signed_in        GET     read      yes        yes
library.create_revision                 signed_in        POST    -         -          -
library.download                        signed_in        GET     -         -          -
library.explore                         signed_in        POST    read      yes        yes
library.export_revision                 signed_in        GET     -         -          -
library.get_document                    signed_in        GET     read      yes        yes
library.inspect_file                    signed_in        -       -         -          -
library.list_documents                  signed_in        GET     read      yes        yes
library.list_revisions                  signed_in        GET     -         -          -
library.preview                         signed_in        GET     -         -          -
library.read_chunks                     signed_in        GET     read      yes        yes
library.read_facets                     signed_in        -       -         -          -
library.read_revision                   signed_in        GET     -         -          -
library.relations                       signed_in        GET     read      yes        yes
library.search                          signed_in        POST    read      yes        yes
maintenance.prune                       public           -       -         -          -
notes.delete_note                       signed_in        DELETE  data      -          -
notes.list_notes                        signed_in        GET     read      yes        yes
notes.read_note                         signed_in        GET     read      yes        yes
notes.write_note                        signed_in        PUT     data      -          -
notifications.job_ended                 public           -       -         -          -
notifications.list_notifications        signed_in        GET     -         -          -
notifications.read_notification         signed_in        POST    -         -          -
skills.list_skills                      signed_in        GET     read      yes        yes
skills.read_skill                       signed_in        GET     -         -          -
skills.read_skill_version               signed_in        GET     read      yes        yes
skills.save_skill                       signed_in        PUT     -         -          -
summarize.selected                      signed_in        POST    -         -          -
summarize.upload                        signed_in        POST    -         -          -
tools.check_calculation                 signed_in        POST    read      yes        yes
tools.list_capabilities                 signed_in        GET     read      yes        yes
tools.make_chart                        signed_in        POST    read      yes        yes
workspace.edit_granted_notebook         signed_in        POST    external  -          -
workspace.edit_notebook                 signed_in        POST    external  -          -
workspace.notebook_run                  signed_in        -       -         -          -
workspace.read_workspace                signed_in        GET     -         -          -
workspace.run_code                      signed_in        POST    external  -          -
workspace.run_in_granted_notebook       signed_in        POST    external  -          -
workspace.run_notebook                  signed_in        POST    external  -          -
workspace.save_chart                    signed_in        POST    external  -          -
workspace.start_workspace               signed_in        POST    -         -          -
workspace.stop_workspace                signed_in        POST    -         -          -
workspace.write_file                    signed_in        PUT     external  -          -
"""
