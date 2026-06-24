"use client";

import { Eye, EyeOff } from "lucide-react";
import { useState, type InputHTMLAttributes } from "react";
import { Input, Label } from "@/components/ui/Input";

interface PasswordFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: string;
}

export function PasswordField({
  label,
  id,
  className = "",
  ...props
}: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);

  return (
    <div>
      <Label htmlFor={id} required={props.required}>
        {label}
      </Label>
      <div className="relative">
        <Input
          {...props}
          id={id}
          type={visible ? "text" : "password"}
          className={`pr-11 ${className}`}
        />
        <button
          type="button"
          onClick={() => setVisible((value) => !value)}
          className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-fg-muted transition-colors hover:text-fg-primary"
          aria-label={visible ? "Hide password" : "Show password"}
          aria-pressed={visible}
        >
          {visible ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </div>
    </div>
  );
}
