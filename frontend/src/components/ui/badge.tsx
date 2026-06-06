import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors",
  {
    variants: {
      variant: {
        default:
          "bg-primary/20 text-primary border border-primary/30",
        secondary:
          "bg-secondary text-secondary-foreground border border-border",
        outline:
          "border border-border text-muted-foreground",
        destructive:
          "bg-red-900/30 text-red-400 border border-red-600/30",
        success:
          "bg-green-900/30 text-green-400 border border-green-600/30",
        warning:
          "bg-amber-900/30 text-amber-400 border border-amber-600/30",
        beta:
          "bg-primary/10 text-primary border border-primary/20 font-mono",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { Badge, badgeVariants }
