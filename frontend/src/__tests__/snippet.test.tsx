import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderSnippet, Snippet, unescapeHtml } from "@/components/Snippet";

describe("snippet renderer", () => {
  it("turns <mark> tags into mark elements", () => {
    const { container } = render(<Snippet text="Total <mark>due</mark>: <mark>4.50</mark> EUR" />);
    const marks = container.querySelectorAll("mark");
    expect(Array.from(marks).map((m) => m.textContent)).toEqual(["due", "4.50"]);
    expect(container.textContent).toBe("Total due: 4.50 EUR");
  });

  it("keeps escaped script text as text and never creates elements from it", () => {
    const { container } = render(
      <Snippet text="before &lt;script&gt;alert(1)&lt;/script&gt; &amp; <mark>after</mark>" />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toBe("before <script>alert(1)</script> & after");
    expect(container.querySelectorAll("mark")).toHaveLength(1);
  });

  it("does not treat other tags as markup", () => {
    const { container } = render(<Snippet text='<b>bold</b> <img src=x onerror="x">' />);
    expect(container.querySelector("b")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<b>bold</b>");
  });

  it("handles plain text, empty input and unknown entities", () => {
    expect(renderSnippet("")).toEqual([]);
    expect(unescapeHtml("&lt;&gt;&amp;&quot;&#x27;&#39;&nbsp;")).toBe(`<>&"''&nbsp;`);
  });
});
