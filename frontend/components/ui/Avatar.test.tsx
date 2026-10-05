import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Avatar, AVATAR_COLOR_KEYS } from "./Avatar";

describe("Avatar initials contrast", () => {
  it.each(AVATAR_COLOR_KEYS)("keeps white initials legible on %s", (color) => {
    const { container } = render(
      <Avatar user={{ username: "contrast@example.test", avatar_color: color }} />,
    );
    const avatar = container.firstElementChild as HTMLElement;
    expect(avatar.classList.contains("text-white")).toBe(true);
    const components = (avatar.style.backgroundColor.match(/\d+/g) ?? []).map(Number);
    expect(components).toHaveLength(3);
    const linear = components.map((component) => {
      const value = component / 255;
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
    const luminance = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
    expect(1.05 / (luminance + 0.05)).toBeGreaterThanOrEqual(4.5);
  });
});
