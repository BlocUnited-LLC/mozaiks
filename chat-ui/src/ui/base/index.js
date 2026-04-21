/**
 * @mozaiks/chat-ui — App UI base component layer (Layer 4)
 *
 * Internal Tailwind + Radix UI components. This layer is NEVER referenced
 * by agents, page schemas, or app definitions. It exists only to provide
 * accessible, styled building blocks consumed by the Mozaiks primitives layer.
 *
 * STATUS: Stub — Layer 4 components will be installed and vendored here in Layer 1.
 *
 * Build order:
 *   1. Install shadcn/ui CLI components (button, card, table, dialog, form, etc.)
 *   2. Each component lives at ./components/{name}.jsx
 *   3. This index re-exports all of them for tree-shaking
 *
 * Agents NEVER import from this path. The primitives layer (../primitives/) is
 * the only caller.
 */

export { Button, buttonVariants } from './components/button.jsx';
export { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from './components/card.jsx';
export { Table, TableHeader, TableBody, TableRow, TableHead, TableCell, TableCaption } from './components/table.jsx';
export { Dialog, DialogTrigger, DialogPortal, DialogClose, DialogOverlay, DialogContent, DialogHeader, DialogFooter, DialogTitle, DialogDescription } from './components/dialog.jsx';
export { Input } from './components/input.jsx';
export { Select, SelectGroup, SelectValue, SelectTrigger, SelectContent, SelectLabel, SelectItem, SelectSeparator } from './components/select.jsx';
export { Badge, badgeVariants } from './components/badge.jsx';
export { Alert, AlertTitle, AlertDescription, alertVariants } from './components/alert.jsx';
export { Progress } from './components/progress.jsx';
export { Skeleton } from './components/skeleton.jsx';

export const _BASE_LAYER_READY = true;
