# Platform Dogfooding Specification

**Status:** Specification
**Created:** 2026-04-07
**Depends on:** MODULAR_ARCHITECTURE_V2.md, UI_SYSTEM_SPEC.md, PLATFORM_SDK_SPEC.md

This document specifies how mozaiks-platform will use the mozaiks runtime to build its own admin dashboard and user interfaces - proving the architecture works by dogfooding it.

---

## Overview

### The Dogfooding Principle

> **If we can't build our own admin dashboard with mozaiks, customers can't build their apps with it either.**

The mozaiks-platform has:
- **.NET services** providing REST APIs (Admin, Apps, Governance, Payment, Hosting, etc.)
- **No standalone admin frontend** - only workflow-embedded React artifacts

The dogfooding opportunity:
- Build the **platform admin dashboard** using mozaiks primitives
- Create **platform modules** that wrap .NET APIs
- Define **admin pages** using YAML schemas
- Run it all on the **mozaiks runtime**

### Capability-Pack Composition

Dogfooding should follow the same capability-first planning contract used for generated apps.

For the platform admin app, that means decomposing the product into canonical packs before deciding whether a surface is a module, page, or workflow:

- `admin_pack` or `crud_pack` for deterministic management surfaces
- `marketplace_pack` for app listings and approvals
- `billing_pack` for transactions and wallets
- `campaigns_pack` for growth and ad operations
- `notifications_pack` for delivery and review surfaces
- optional agentic extensions only where operator review or reasoning is actually needed

Platform modules remain the deterministic backbone for these packs. Workflows are attached for agentic augmentation, not used as the default implementation unit.

### Architecture Summary

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      PLATFORM DOGFOODING STACK                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    MOZAIKS RUNTIME (Python)                          │   │
│  │  ┌───────────────────────────────────────────────────────────────┐  │   │
│  │  │ Platform Admin App                                             │  │   │
│  │  │                                                                │  │   │
│  │  │  pages/           modules/           workflows/                │  │   │
│  │  │  ├── dashboard    ├── platform.users  ├── AppApproval         │  │   │
│  │  │  ├── users        ├── platform.apps   ├── UserManagement      │  │   │
│  │  │  ├── apps         ├── platform.gov    ├── GovernanceReview    │  │   │
│  │  │  └── governance   └── platform.billing└── BillingOps          │  │   │
│  │  │                                                                │  │   │
│  │  └───────────────────────────────────────────────────────────────┘  │   │
│  │                              │                                       │   │
│  │                              │ Module Executor                       │   │
│  │                              ▼                                       │   │
│  │  ┌───────────────────────────────────────────────────────────────┐  │   │
│  │  │                    PLATFORM SDK (Python)                       │  │   │
│  │  │  AdminClient │ AppsClient │ GovernanceClient │ PaymentClient  │  │   │
│  │  └───────────────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    │ HTTP/REST                              │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    .NET SERVICES (Unchanged)                         │   │
│  │  Admin.API │ Apps.API │ Governance.API │ Payment.API │ Hosting.API  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Platform Modules

Platform modules are Python modules that wrap .NET service APIs. They follow the standard module pattern but call external services instead of local MongoDB.

### Module Registry

| Module | .NET Service | Purpose |
|--------|-------------|---------|
| `platform.users` | Admin.API | User management, suspension, roles |
| `platform.apps` | Apps.API + Admin.API | App lifecycle, approvals |
| `platform.governance` | Governance.API | Proposals, funding rounds, investments |
| `platform.billing` | Payment.API | Billing, wallets, ledger |
| `platform.hosting` | Hosting.API | Provisioning, domains |
| `platform.discovery` | Discovery.API | Categories, featured, search |
| `platform.growth` | Growth.API | Campaigns, ads, subscribers |
| `platform.teams` | Teams.API | Team management, invitations |
| `platform.notifications` | Notification.API | Email, push notifications |
| `platform.stats` | Admin.API | Dashboard statistics |

### Module Structure

```
modules/
├── platform/
│   ├── __init__.py
│   ├── base.py              # Base client with auth handling
│   ├── users.py             # platform.users module
│   ├── apps.py              # platform.apps module
│   ├── governance.py        # platform.governance module
│   ├── billing.py           # platform.billing module
│   ├── hosting.py           # platform.hosting module
│   ├── discovery.py         # platform.discovery module
│   ├── growth.py            # platform.growth module
│   ├── teams.py             # platform.teams module
│   ├── notifications.py     # platform.notifications module
│   └── stats.py             # platform.stats module
└── module.yaml              # Module manifest
```

### Module Definition: platform.users

```yaml
# modules/platform/users.module.yaml
name: platform.users
version: "1.0"
description: Platform user management

# No local collection - calls external API
external: true
service: admin

actions:
  # Queries
  - name: list
    type: query
    description: List platform users with filtering
    params:
      - name: role
        type: string
        optional: true
        enum: [user, admin, superadmin]
      - name: status
        type: string
        optional: true
        enum: [active, suspended]
      - name: search
        type: string
        optional: true
      - name: page
        type: integer
        default: 1
      - name: limit
        type: integer
        default: 20
    returns:
      type: paginated
      item_type: User

  - name: get
    type: query
    description: Get user by ID
    params:
      - name: user_id
        type: string
        required: true
    returns:
      type: User

  - name: stats
    type: query
    description: Get user statistics
    returns:
      type: UserStats

  # Mutations
  - name: suspend
    type: mutation
    description: Suspend a user
    params:
      - name: user_id
        type: string
        required: true
      - name: reason
        type: string
        required: true
    emits:
      - platform.user.suspended

  - name: unsuspend
    type: mutation
    description: Restore a suspended user
    params:
      - name: user_id
        type: string
        required: true
    emits:
      - platform.user.unsuspended

  - name: update_role
    type: mutation
    description: Update user role
    params:
      - name: user_id
        type: string
        required: true
      - name: role
        type: string
        required: true
        enum: [user, admin, superadmin]
    emits:
      - platform.user.role_changed
```

### Module Implementation: platform.users

```python
# modules/platform/users.py
from mozaiks_core import Module, Action, Event
from mozaiks_platform_sdk import AdminClient
from typing import Optional, List
from pydantic import BaseModel

class User(BaseModel):
    id: str
    email: str
    name: str
    role: str
    status: str
    created_at: str
    last_login: Optional[str]

class UserStats(BaseModel):
    total_users: int
    active_users: int
    suspended_users: int
    admins: int
    new_this_month: int

class PaginatedUsers(BaseModel):
    items: List[User]
    total: int
    page: int
    limit: int
    has_more: bool

class UsersModule(Module):
    """Platform user management module."""

    name = "platform.users"

    def __init__(self):
        self._client: Optional[AdminClient] = None

    def _get_client(self, ctx) -> AdminClient:
        """Get or create admin client with context auth."""
        if not self._client:
            self._client = AdminClient(
                base_url=ctx.platform_url,
                auth_token=ctx.auth_token
            )
        return self._client

    # =========================================================================
    # QUERIES
    # =========================================================================

    @Action(type="query")
    async def list(
        self,
        ctx,
        role: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 20
    ) -> PaginatedUsers:
        """List platform users with filtering."""
        client = self._get_client(ctx)

        response = await client.get(
            "/api/admin/users",
            params={
                "role": role,
                "status": status,
                "search": search,
                "page": page,
                "limit": limit
            }
        )

        return PaginatedUsers(
            items=[User(**u) for u in response["items"]],
            total=response["total"],
            page=page,
            limit=limit,
            has_more=response["total"] > page * limit
        )

    @Action(type="query")
    async def get(self, ctx, user_id: str) -> User:
        """Get user by ID."""
        client = self._get_client(ctx)
        response = await client.get(f"/api/admin/users/{user_id}")
        return User(**response)

    @Action(type="query")
    async def stats(self, ctx) -> UserStats:
        """Get user statistics for dashboard."""
        client = self._get_client(ctx)
        response = await client.get("/api/admin/users/stats")
        return UserStats(**response)

    # =========================================================================
    # MUTATIONS
    # =========================================================================

    @Action(type="mutation")
    async def suspend(self, ctx, user_id: str, reason: str) -> User:
        """Suspend a user."""
        client = self._get_client(ctx)

        response = await client.post(
            f"/api/admin/users/{user_id}/suspend",
            json={"reason": reason}
        )

        # Emit domain event
        await ctx.emit(Event.create(
            event_type="platform.user.suspended",
            payload={
                "user_id": user_id,
                "reason": reason,
                "suspended_by": ctx.user_id
            },
            tenant={"app_id": ctx.app_id, "user_id": ctx.user_id}
        ))

        return User(**response)

    @Action(type="mutation")
    async def unsuspend(self, ctx, user_id: str) -> User:
        """Restore a suspended user."""
        client = self._get_client(ctx)

        response = await client.post(f"/api/admin/users/{user_id}/unsuspend")

        await ctx.emit(Event.create(
            event_type="platform.user.unsuspended",
            payload={
                "user_id": user_id,
                "restored_by": ctx.user_id
            },
            tenant={"app_id": ctx.app_id, "user_id": ctx.user_id}
        ))

        return User(**response)

    @Action(type="mutation")
    async def update_role(self, ctx, user_id: str, role: str) -> User:
        """Update user role."""
        client = self._get_client(ctx)

        response = await client.patch(
            f"/api/admin/users/{user_id}/role",
            json={"role": role}
        )

        await ctx.emit(Event.create(
            event_type="platform.user.role_changed",
            payload={
                "user_id": user_id,
                "new_role": role,
                "changed_by": ctx.user_id
            },
            tenant={"app_id": ctx.app_id, "user_id": ctx.user_id}
        ))

        return User(**response)
```

### Module Definition: platform.apps

```yaml
# modules/platform/apps.module.yaml
name: platform.apps
version: "1.0"
description: Platform app management

external: true
service: apps

actions:
  # Queries
  - name: list
    type: query
    description: List all apps
    params:
      - name: status
        type: string
        optional: true
        enum: [pending, approved, rejected, live, suspended]
      - name: page
        type: integer
        default: 1
      - name: limit
        type: integer
        default: 20
    returns:
      type: paginated
      item_type: App

  - name: pending
    type: query
    description: List apps pending approval
    returns:
      type: list
      item_type: App

  - name: get
    type: query
    description: Get app by ID
    params:
      - name: app_id
        type: string
        required: true
    returns:
      type: App

  - name: stats
    type: query
    description: Get app statistics
    returns:
      type: AppStats

  # Mutations
  - name: approve
    type: mutation
    description: Approve an app
    params:
      - name: app_id
        type: string
        required: true
      - name: notes
        type: string
        optional: true
    emits:
      - platform.app.approved

  - name: reject
    type: mutation
    description: Reject an app
    params:
      - name: app_id
        type: string
        required: true
      - name: reason
        type: string
        required: true
    emits:
      - platform.app.rejected

  - name: suspend
    type: mutation
    description: Suspend a live app
    params:
      - name: app_id
        type: string
        required: true
      - name: reason
        type: string
        required: true
    emits:
      - platform.app.suspended

  - name: promote
    type: mutation
    description: Promote app to featured/hero
    params:
      - name: app_id
        type: string
        required: true
      - name: promotion_type
        type: string
        required: true
        enum: [hero, featured, boosted]
      - name: duration_days
        type: integer
        default: 7
    emits:
      - platform.app.promoted
```

### Module Definition: platform.stats

```yaml
# modules/platform/stats.module.yaml
name: platform.stats
version: "1.0"
description: Platform dashboard statistics

external: true
service: admin

actions:
  - name: overview
    type: query
    description: Get platform overview stats
    returns:
      type: PlatformOverview

  - name: revenue
    type: query
    description: Get revenue statistics
    params:
      - name: period
        type: string
        default: "30d"
        enum: [7d, 30d, 90d, 1y]
    returns:
      type: RevenueStats

  - name: growth
    type: query
    description: Get growth metrics
    params:
      - name: period
        type: string
        default: "30d"
    returns:
      type: GrowthStats
```

---

## 2. Platform Admin Pages

Admin pages are defined using YAML schemas and rendered by the mozaiks runtime using primitives.

### Page Structure

```
pages/
├── admin/
│   ├── dashboard.yaml        # Main dashboard
│   ├── users.yaml            # User management
│   ├── users/
│   │   └── [id].yaml         # User detail (dynamic route)
│   ├── apps.yaml             # App management
│   ├── apps/
│   │   ├── pending.yaml      # Pending approvals
│   │   └── [id].yaml         # App detail
│   ├── governance.yaml       # Governance overview
│   ├── governance/
│   │   ├── proposals.yaml    # Active proposals
│   │   ├── funding.yaml      # Funding rounds
│   │   └── [id].yaml         # Proposal detail
│   ├── billing.yaml          # Billing overview
│   └── settings.yaml         # Platform settings
└── navigation.yaml           # Admin navigation
```

### Dashboard Page

```yaml
# pages/admin/dashboard.yaml
type: Page
title: Platform Dashboard
layout: dashboard
access:
  roles: [admin, superadmin]

# Page-level data loading
data:
  overview:
    source: "module:platform.stats:overview"
  pending_apps:
    source: "module:platform.apps:pending"
  recent_users:
    source: "module:platform.users:list"
    params:
      limit: 5
      sort: "-created_at"

sections:
  # =========================================================================
  # STATS ROW
  # =========================================================================
  - type: Section
    title: Overview
    children:
      - type: StatGroup
        columns: 4
        stats:
          - key: total_users
            label: Total Users
            value: "{{ data.overview.total_users }}"
            format: number
            icon: users
            trend:
              value: "{{ data.overview.users_growth_percent }}"
              direction: "{{ data.overview.users_growth_percent > 0 ? 'up' : 'down' }}"

          - key: active_apps
            label: Active Apps
            value: "{{ data.overview.active_apps }}"
            format: number
            icon: grid

          - key: pending_approvals
            label: Pending Approvals
            value: "{{ data.overview.pending_approvals }}"
            format: number
            icon: clock
            variant: "{{ data.overview.pending_approvals > 10 ? 'warning' : 'default' }}"
            action:
              label: Review
              href: /admin/apps/pending

          - key: monthly_revenue
            label: Monthly Revenue
            value: "{{ data.overview.monthly_revenue }}"
            format: currency
            icon: dollar-sign

  # =========================================================================
  # PENDING APPROVALS
  # =========================================================================
  - type: Section
    title: Pending App Approvals
    collapsible: true
    actions:
      - label: View All
        href: /admin/apps/pending
    children:
      - type: DataTable
        data: "{{ data.pending_apps }}"
        columns:
          - key: name
            label: App Name
            render:
              type: link
              href: "/admin/apps/{{ row.id }}"

          - key: owner.name
            label: Owner

          - key: submitted_at
            label: Submitted
            format: relative_time

          - key: category
            label: Category
            render:
              type: Badge

        row_actions:
          - label: Approve
            variant: success
            action: "workflow:AppApproval:approve"
            params:
              app_id: "{{ row.id }}"

          - label: Reject
            variant: destructive
            action: "workflow:AppApproval:reject"
            params:
              app_id: "{{ row.id }}"

        empty_state:
          icon: check-circle
          title: All caught up!
          description: No apps pending approval

  # =========================================================================
  # RECENT USERS
  # =========================================================================
  - type: Section
    title: Recent Users
    actions:
      - label: View All
        href: /admin/users
    children:
      - type: DataTable
        data: "{{ data.recent_users.items }}"
        columns:
          - key: name
            label: Name
            render:
              type: link
              href: "/admin/users/{{ row.id }}"

          - key: email
            label: Email

          - key: role
            label: Role
            render:
              type: Badge
              variant: "{{ row.role === 'admin' ? 'secondary' : 'default' }}"

          - key: created_at
            label: Joined
            format: relative_time

  # =========================================================================
  # QUICK ACTIONS
  # =========================================================================
  - type: Section
    title: Quick Actions
    children:
      - type: Grid
        columns: 3
        gap: 4
        children:
          - type: Card
            variant: interactive
            children:
              - type: Stack
                align: center
                children:
                  - type: Icon
                    name: user-plus
                    size: lg
                  - type: Text
                    content: Invite Admin
                  - type: Text
                    variant: muted
                    content: Add new platform admin
            action:
              type: modal
              modal: InviteAdminModal

          - type: Card
            variant: interactive
            children:
              - type: Stack
                align: center
                children:
                  - type: Icon
                    name: megaphone
                    size: lg
                  - type: Text
                    content: Create Campaign
                  - type: Text
                    variant: muted
                    content: Launch growth campaign
            action:
              href: /admin/growth/campaigns/new

          - type: Card
            variant: interactive
            children:
              - type: Stack
                align: center
                children:
                  - type: Icon
                    name: flag
                    size: lg
                  - type: Text
                    content: Feature App
                  - type: Text
                    variant: muted
                    content: Promote to featured
            action:
              type: modal
              modal: FeatureAppModal
```

### Users Page

```yaml
# pages/admin/users.yaml
type: Page
title: User Management
layout: list
access:
  roles: [admin, superadmin]

data:
  users:
    source: "module:platform.users:list"
    params:
      page: "{{ query.page || 1 }}"
      limit: 20
      role: "{{ query.role }}"
      status: "{{ query.status }}"
      search: "{{ query.search }}"

# Page-level filters
filters:
  - key: search
    type: search
    placeholder: Search users...

  - key: role
    type: select
    label: Role
    options:
      - { value: "", label: "All Roles" }
      - { value: "user", label: "User" }
      - { value: "admin", label: "Admin" }
      - { value: "superadmin", label: "Super Admin" }

  - key: status
    type: select
    label: Status
    options:
      - { value: "", label: "All Status" }
      - { value: "active", label: "Active" }
      - { value: "suspended", label: "Suspended" }

sections:
  - type: DataTable
    data: "{{ data.users.items }}"
    selection: multi

    columns:
      - key: name
        label: Name
        sortable: true
        render:
          type: Stack
          direction: row
          align: center
          gap: 2
          children:
            - type: Avatar
              src: "{{ row.avatar }}"
              fallback: "{{ row.name | initials }}"
            - type: link
              content: "{{ row.name }}"
              href: "/admin/users/{{ row.id }}"

      - key: email
        label: Email
        sortable: true

      - key: role
        label: Role
        sortable: true
        render:
          type: Badge
          content: "{{ row.role | titlecase }}"
          variant: "{{ row.role === 'superadmin' ? 'destructive' : row.role === 'admin' ? 'secondary' : 'default' }}"

      - key: status
        label: Status
        render:
          type: Badge
          content: "{{ row.status | titlecase }}"
          variant: "{{ row.status === 'active' ? 'success' : 'warning' }}"

      - key: created_at
        label: Joined
        sortable: true
        format: date

      - key: last_login
        label: Last Login
        format: relative_time

    row_actions:
      - label: View
        icon: eye
        href: "/admin/users/{{ row.id }}"

      - label: Edit Role
        icon: shield
        action:
          type: modal
          modal: EditRoleModal
          params:
            user_id: "{{ row.id }}"
            current_role: "{{ row.role }}"
        visible: "{{ user.role === 'superadmin' }}"

      - label: Suspend
        icon: ban
        variant: destructive
        action:
          type: modal
          modal: SuspendUserModal
          params:
            user_id: "{{ row.id }}"
        visible: "{{ row.status === 'active' }}"

      - label: Restore
        icon: check
        action: "module:platform.users:unsuspend"
        params:
          user_id: "{{ row.id }}"
        visible: "{{ row.status === 'suspended' }}"

    bulk_actions:
      - label: Suspend Selected
        icon: ban
        variant: destructive
        action:
          type: modal
          modal: BulkSuspendModal
        requires_selection: true

    pagination:
      total: "{{ data.users.total }}"
      page: "{{ data.users.page }}"
      limit: "{{ data.users.limit }}"

# Modals defined for this page
modals:
  EditRoleModal:
    title: Edit User Role
    children:
      - type: Form
        action: "module:platform.users:update_role"
        fields:
          - name: user_id
            type: hidden
            value: "{{ params.user_id }}"

          - name: role
            type: select
            label: New Role
            default: "{{ params.current_role }}"
            options:
              - { value: "user", label: "User" }
              - { value: "admin", label: "Admin" }
              - { value: "superadmin", label: "Super Admin" }

        submit_label: Update Role
        on_success:
          toast: "Role updated successfully"
          close_modal: true
          refresh: true

  SuspendUserModal:
    title: Suspend User
    variant: destructive
    children:
      - type: Alert
        variant: warning
        content: This will prevent the user from accessing the platform.

      - type: Form
        action: "module:platform.users:suspend"
        fields:
          - name: user_id
            type: hidden
            value: "{{ params.user_id }}"

          - name: reason
            type: textarea
            label: Reason for Suspension
            required: true
            placeholder: Explain why this user is being suspended...

        submit_label: Suspend User
        submit_variant: destructive
        on_success:
          toast: "User suspended"
          close_modal: true
          refresh: true
```

### App Approvals Page

```yaml
# pages/admin/apps/pending.yaml
type: Page
title: Pending App Approvals
layout: list
access:
  roles: [admin, superadmin]

data:
  apps:
    source: "module:platform.apps:pending"

sections:
  - type: Section
    children:
      - type: Alert
        variant: info
        icon: info
        content: "{{ data.apps.length }} apps are waiting for review"
        visible: "{{ data.apps.length > 0 }}"

      - type: DataTable
        data: "{{ data.apps }}"
        columns:
          - key: name
            label: App Name
            render:
              type: Stack
              direction: row
              gap: 3
              children:
                - type: Avatar
                  src: "{{ row.icon }}"
                  fallback: "{{ row.name | initials }}"
                  shape: square
                - type: Stack
                  gap: 0
                  children:
                    - type: Text
                      content: "{{ row.name }}"
                      weight: medium
                    - type: Text
                      content: "{{ row.tagline }}"
                      variant: muted
                      size: sm

          - key: owner
            label: Submitted By
            render:
              type: Stack
              gap: 0
              children:
                - type: Text
                  content: "{{ row.owner.name }}"
                - type: Text
                  content: "{{ row.owner.email }}"
                  variant: muted
                  size: sm

          - key: category
            label: Category
            render:
              type: Badge

          - key: submitted_at
            label: Submitted
            format: relative_time

        row_actions:
          - label: Review
            icon: eye
            href: "/admin/apps/{{ row.id }}"

          - label: Quick Approve
            icon: check
            variant: success
            action: "module:platform.apps:approve"
            params:
              app_id: "{{ row.id }}"
            confirm:
              title: Approve App?
              message: "Are you sure you want to approve {{ row.name }}?"

          - label: Reject
            icon: x
            variant: destructive
            action:
              type: modal
              modal: RejectAppModal
              params:
                app_id: "{{ row.id }}"
                app_name: "{{ row.name }}"

        empty_state:
          icon: check-circle
          title: No pending approvals
          description: All apps have been reviewed

modals:
  RejectAppModal:
    title: "Reject {{ params.app_name }}"
    variant: destructive
    children:
      - type: Form
        action: "module:platform.apps:reject"
        fields:
          - name: app_id
            type: hidden
            value: "{{ params.app_id }}"

          - name: reason
            type: textarea
            label: Rejection Reason
            required: true
            placeholder: Explain why this app is being rejected...
            hint: This will be sent to the app owner

        submit_label: Reject App
        submit_variant: destructive
        on_success:
          toast: "App rejected"
          close_modal: true
          refresh: true
```

### Navigation Definition

```yaml
# pages/navigation.yaml
type: Navigation
variant: sidebar

brand:
  name: Mozaiks Admin
  logo: /images/mozaiks-logo.svg
  href: /admin/dashboard

items:
  - label: Dashboard
    icon: home
    href: /admin/dashboard

  - label: Users
    icon: users
    href: /admin/users
    badge:
      value: "{{ stats.suspended_users }}"
      variant: warning
      visible: "{{ stats.suspended_users > 0 }}"

  - label: Apps
    icon: grid
    children:
      - label: All Apps
        href: /admin/apps
      - label: Pending Approval
        href: /admin/apps/pending
        badge:
          value: "{{ stats.pending_approvals }}"
          variant: warning
      - label: Featured
        href: /admin/apps/featured
      - label: Suspended
        href: /admin/apps/suspended

  - label: Governance
    icon: vote
    children:
      - label: Proposals
        href: /admin/governance/proposals
      - label: Funding Rounds
        href: /admin/governance/funding
      - label: Investments
        href: /admin/governance/investments

  - label: Growth
    icon: trending-up
    children:
      - label: Campaigns
        href: /admin/growth/campaigns
      - label: Analytics
        href: /admin/growth/analytics
      - label: Subscribers
        href: /admin/growth/subscribers

  - label: Billing
    icon: credit-card
    href: /admin/billing

  - type: divider

  - label: Settings
    icon: settings
    href: /admin/settings
    position: bottom

# User menu (top right)
user_menu:
  items:
    - label: Profile
      icon: user
      href: /admin/profile
    - label: Notifications
      icon: bell
      href: /admin/notifications
      badge:
        value: "{{ notifications.unread }}"
    - type: divider
    - label: Sign Out
      icon: log-out
      action: "auth:logout"
```

---

## 3. Platform App Definition

The platform admin is defined as a mozaiks app.

```yaml
# app.yaml - Platform Admin App
name: mozaiks-platform-admin
version: "1.0"
description: Mozaiks Platform Administration Dashboard

# ============================================================================
# CAPABILITIES
# ============================================================================
capabilities:
  ai: true          # AI workflows for complex operations
  modules: true     # Platform modules for data operations

# ============================================================================
# THEME
# ============================================================================
theme:
  primary: indigo
  variant: modern
  radius: medium
  appearance: system
  font: inter

# ============================================================================
# MODULES
# ============================================================================
modules:
  # Platform modules (external API wrappers)
  - platform.users
  - platform.apps
  - platform.governance
  - platform.billing
  - platform.hosting
  - platform.discovery
  - platform.growth
  - platform.teams
  - platform.notifications
  - platform.stats

# ============================================================================
# WORKFLOWS
# ============================================================================
workflows:
  - AppApproval           # App review and approval workflow
  - UserManagement        # Complex user operations
  - GovernanceReview      # Proposal voting assistance
  - BillingOperations     # Refunds, adjustments
  - PromotionManager      # Featured/hero app management

# ============================================================================
# PAGES
# ============================================================================
pages:
  - admin/dashboard
  - admin/users
  - admin/users/[id]
  - admin/apps
  - admin/apps/pending
  - admin/apps/[id]
  - admin/governance
  - admin/governance/proposals
  - admin/governance/funding
  - admin/governance/[id]
  - admin/billing
  - admin/settings

# ============================================================================
# NAVIGATION
# ============================================================================
navigation:
  source: pages/navigation.yaml

# ============================================================================
# AUTH
# ============================================================================
auth:
  provider: keycloak
  required: true
  roles:
    - admin
    - superadmin

# ============================================================================
# CONTEXT
# ============================================================================
context:
  # Platform API endpoint
  platform_url: "${PLATFORM_API_URL}"

  # Injected at runtime
  app_id: "${APP_ID}"

  # Feature flags
  features:
    governance_enabled: true
    growth_enabled: true
    billing_enabled: true
```

---

## 4. Migration Path

### Phase 1: Platform SDK Enhancement

Update the Python Platform SDK to support all required endpoints:

```python
# mozaiks_platform_sdk/clients/admin.py

class AdminClient(BaseClient):
    """Client for Admin.API service."""

    async def get_users(
        self,
        role: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 20
    ) -> PaginatedResponse:
        """GET /api/admin/users"""
        return await self.get("/api/admin/users", params={
            "role": role,
            "status": status,
            "search": search,
            "page": page,
            "limit": limit
        })

    async def suspend_user(self, user_id: str, reason: str) -> dict:
        """POST /api/admin/users/{user_id}/suspend"""
        return await self.post(
            f"/api/admin/users/{user_id}/suspend",
            json={"reason": reason}
        )

    async def unsuspend_user(self, user_id: str) -> dict:
        """POST /api/admin/users/{user_id}/unsuspend"""
        return await self.post(f"/api/admin/users/{user_id}/unsuspend")

    async def get_pending_apps(self) -> List[dict]:
        """GET /api/admin/apps/pending"""
        return await self.get("/api/admin/apps/pending")

    async def approve_app(self, app_id: str, notes: Optional[str] = None) -> dict:
        """POST /api/admin/apps/{app_id}/review"""
        return await self.post(
            f"/api/admin/apps/{app_id}/review",
            json={"decision": "approved", "notes": notes}
        )

    async def reject_app(self, app_id: str, reason: str) -> dict:
        """POST /api/admin/apps/{app_id}/review"""
        return await self.post(
            f"/api/admin/apps/{app_id}/review",
            json={"decision": "rejected", "reason": reason}
        )

    async def get_platform_stats(self) -> dict:
        """GET /api/admin/dashboard"""
        return await self.get("/api/admin/dashboard")
```

### Phase 2: Platform Modules

Create the platform module package:

```
packages/
└── platform-modules/
    ├── pyproject.toml
    └── src/
        └── mozaiks_platform_modules/
            ├── __init__.py
            ├── users.py
            ├── apps.py
            ├── governance.py
            ├── billing.py
            ├── hosting.py
            └── stats.py
```

### Phase 3: Admin Pages

Create the admin page definitions:

```
apps/
└── platform-admin/
    ├── app.yaml
    ├── pages/
    │   ├── navigation.yaml
    │   └── admin/
    │       ├── dashboard.yaml
    │       ├── users.yaml
    │       └── ...
    ├── modules/
    │   └── (uses platform-modules package)
    └── workflows/
        ├── AppApproval/
        └── ...
```

### Phase 4: Deployment

```yaml
# Deploy platform admin as a mozaiks app
# docker-compose.yml

services:
  platform-admin:
    image: mozaiks-runtime:latest
    environment:
      - APP_PATH=/app/platform-admin
      - PLATFORM_API_URL=http://api-gateway:8080
      - KEYCLOAK_URL=http://keycloak:8080
    volumes:
      - ./apps/platform-admin:/app/platform-admin
    depends_on:
      - api-gateway
      - keycloak
```

---

## 5. Verification Checklist

### Platform Modules
- [ ] `platform.users` module calls Admin.API correctly
- [ ] `platform.apps` module handles approvals
- [ ] `platform.governance` module reads proposals
- [ ] `platform.billing` module shows transactions
- [ ] `platform.stats` module returns dashboard data
- [ ] All modules emit domain events
- [ ] Auth tokens pass through correctly

### Admin Pages
- [ ] Dashboard renders with real data
- [ ] User list with filtering works
- [ ] App approval workflow completes
- [ ] Modals open and submit correctly
- [ ] Navigation works between pages
- [ ] Role-based access enforced

### Integration
- [ ] Platform admin runs on mozaiks runtime
- [ ] Actions trigger .NET service calls
- [ ] Events flow to platform event bus
- [ ] Errors handled gracefully
- [ ] Performance acceptable (<500ms page load)

---

## 6. Success Criteria

The dogfooding is successful when:

1. **Platform admin is fully functional**
   - All current Admin.API features accessible
   - No degradation from hypothetical "native" UI

2. **Proves the architecture**
   - Modules wrap external APIs cleanly
   - Pages render complex admin UI
   - Workflows handle multi-step operations
   - No hacks or workarounds needed

3. **Developer experience validated**
   - YAML page definitions are readable
   - Module pattern scales to many services
   - Testing is straightforward

4. **Performance acceptable**
   - Dashboard loads in <1s
   - Table pagination smooth
   - Modal interactions snappy

---

## Summary

| Layer | What | Status |
|-------|------|--------|
| .NET Services | Admin.API, Apps.API, etc. | KEEP (unchanged) |
| Platform SDK | Python HTTP clients | ENHANCE |
| Platform Modules | Module wrappers for APIs | BUILD NEW |
| Admin Pages | YAML page definitions | BUILD NEW |
| Mozaiks Runtime | Renders pages, executes modules | USE (from packages/) |

This spec provides the blueprint for dogfooding mozaiks by building the platform's own admin dashboard using the mozaiks architecture.
