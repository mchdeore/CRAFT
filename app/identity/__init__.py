"""craft-id: a small stand-in for Microsoft Entra ID that CRAFT runs itself.

It speaks the same OpenID Connect v2.0 shapes as Entra (paths, claims, RS256 signing), so the
switch to Entra changes craft-app's settings, not its code. Its tables are readable only by the
craft_id database role; craft-app never sees its accounts or passwords.
"""
