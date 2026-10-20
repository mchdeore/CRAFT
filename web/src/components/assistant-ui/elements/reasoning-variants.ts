import { cva } from "class-variance-authority";

export const ANIMATION_DURATION = 200;

export const reasoningVariants = cva("aui-reasoning-root mb-4 w-full", {
  variants: {
    variant: {
      outline: "rounded-lg border px-3 py-2",
      ghost: "",
      muted: "bg-muted/50 rounded-lg px-3 py-2",
    },
  },
  defaultVariants: {
    variant: "outline",
  },
});
