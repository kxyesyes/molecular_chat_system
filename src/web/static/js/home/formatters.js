"use strict";

window.HomeFormatters = (function () {
  const Safe = window.MedChatSafeRender || {
    escapeHtml: function (content) {
      return String(content)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/'/g, "&#039;")
        .replace(/"/g, "&quot;");
    },
    safeUrl: function () {
      return "#";
    },
  };

  function escapeHtml(content) {
    return Safe.escapeHtml(content);
  }

  function withCardFrame(icon, title, subtitle, gradient, bodyHtml) {
    return `
      <div style="background: ${gradient}; border-radius: 16px; padding: 24px; margin: 16px 0;">
        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
          <div style="width: 48px; height: 48px; background: rgba(255,255,255,0.78); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 24px;">${icon}</div>
          <div>
            <h3 style="margin: 0; color: #2d3748; font-size: 20px;">${title}</h3>
            <p style="margin: 4px 0 0 0; color: #718096; font-size: 14px;">${subtitle}</p>
          </div>
        </div>
        ${bodyHtml}
      </div>
    `;
  }

  function withWhitePanel(bodyHtml) {
    return `
      <div style="background: white; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
        ${bodyHtml}
      </div>
    `;
  }

  function renderMarkdownLike(content) {
    return escapeHtml(content)
      .replace(
        /```(.*?)\n([\s\S]*?)```/g,
        '<pre style="background: #f7fafc; padding: 12px; border-radius: 8px; overflow-x: auto; margin: 12px 0;"><code class="language-$1">$2</code></pre>',
      )
      .replace(
        /`([^`]+)`/g,
        '<code style="background: #edf2f7; padding: 3px 6px; border-radius: 4px; font-size: 0.9em; color: #e53e3e;">$1</code>',
      )
      .replace(
        /\*\*(.*?)\*\*/g,
        '<strong style="color: #2d3748; font-weight: 600;">$1</strong>',
      )
      .replace(/\*(.*?)\*/g, "<em>$1</em>")
      .replace(
        /\[([^\]]+)\]\(([^)]+)\)/g,
        function (_, label, url) {
          return (
            '<a href="' +
            Safe.safeUrl(url) +
            '" target="_blank" rel="noopener noreferrer" style="color: #4299e1; text-decoration: underline;">' +
            escapeHtml(label) +
            "</a>"
          );
        },
      )
      .replace(/\n/g, "<br>");
  }

  function formatSynthesisRoute(content) {
    const routeMatches = content.match(
      /\*\*路线\s*\d+\*\*[\s\S]*?(?=\*\*路线\s*\d+\*\*|$)/g,
    );

    if (!routeMatches || routeMatches.length === 0) {
      return renderMarkdownLike(content);
    }

    let routesHtml = "";

    routeMatches.forEach(function (route, index) {
      const confidenceMatch = route.match(/置信[度息]?\s*[:：]?\s*(\d+\.?\d*%)/);
      const confidence = confidenceMatch ? confidenceMatch[1] : "未知";
      const stepMatches = route.match(
        /步骤\s*\d+[:：]\s*`([^`]+)`\s*(?:→|->|=>)\s*`([^`]+)`/g,
      );

      let stepsHtml = "";
      if (stepMatches && stepMatches.length) {
        stepMatches.forEach(function (step) {
          const stepMatch = step.match(
            /步骤\s*\d+[:：]\s*`([^`]+)`\s*(?:→|->|=>)\s*`([^`]+)`/,
          );
          if (!stepMatch) return;

          stepsHtml += `
            <div style="display: flex; align-items: center; margin: 12px 0; padding: 12px; background: #f8fafc; border-radius: 8px;">
              <div style="background: #e2e8f0; padding: 8px 12px; border-radius: 6px; font-family: monospace; font-size: 12px; color: #4a5568; margin-right: 12px; flex: 1;">
                ${escapeHtml(stepMatch[1])}
              </div>
              <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 6px 12px; border-radius: 20px; font-size: 20px; margin: 0 12px;">→</div>
              <div style="background: #e6fffa; padding: 8px 12px; border-radius: 6px; font-family: monospace; font-size: 12px; color: #234e52; flex: 1;">
                ${escapeHtml(stepMatch[2])}
              </div>
            </div>
          `;
        });
      } else {
        stepsHtml = `
          <div style="padding: 12px; background: #fdf2e9; border-radius: 8px; color: #744210; text-align: center;">
            暂无详细步骤信息
          </div>
        `;
      }

      routesHtml += `
        <div style="background: white; border-radius: 12px; padding: 20px; margin: 16px 0; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
          <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px;">
            <h4 style="margin: 0; color: #2d3748; font-size: 18px;">路线 ${index + 1}</h4>
            <div style="background: linear-gradient(135deg, #48bb78 0%, #38a169 100%); color: white; padding: 6px 12px; border-radius: 20px; font-size: 14px; font-weight: bold;">
              置信度 ${confidence}
            </div>
          </div>
          ${stepsHtml}
        </div>
      `;
    });

    const noteHtml = `
      <div style="margin-top: 20px; padding: 16px; background: rgba(102, 126, 234, 0.1); border-radius: 8px; border-left: 4px solid #667eea;">
        <p style="margin: 0; font-size: 14px; color: #4a5568;">
          <strong>说明：</strong> 合成路线按整体置信度排序，并结合了已知反应先例与可行性。
        </p>
      </div>
    `;

    return withCardFrame(
      "🧪",
      "逆合成分析结果",
      "IBM RXN for Chemistry",
      "linear-gradient(135deg, #667eea20 0%, #764ba220 100%)",
      routesHtml + noteHtml,
    );
  }

  function formatReactionPrediction(content) {
    const reactantMatch = content.match(/反应物\s*[:：]?\s*`([^`]+)`/);
    const productMatches = content.match(
      /\d+\.\s*`([^`]+)`\s*\((?:置信度\s*)?(\d+\.?\d*)%\)/g,
    );

    if (!reactantMatch && !productMatches) {
      return renderMarkdownLike(content);
    }

    let bodyHtml = "";
    if (reactantMatch) {
      bodyHtml += `
        <div style="background: white; border-radius: 12px; padding: 20px; margin: 16px 0; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
          <h4 style="margin: 0 0 12px 0; color: #2d3748;">反应物</h4>
          <div style="background: #f0f9ff; padding: 12px; border-radius: 8px; font-family: monospace; font-size: 16px; color: #0c4a6e; border: 2px solid #7dd3fc;">
            ${escapeHtml(reactantMatch[1])}
          </div>
        </div>
      `;
    }

    if (productMatches && productMatches.length) {
      let productsHtml = "";
      productMatches.forEach(function (product, index) {
        const productMatch = product.match(
          /\d+\.\s*`([^`]+)`\s*\((?:置信度\s*)?(\d+\.?\d*)%\)/,
        );
        if (!productMatch) return;

        const confidenceNum = parseFloat(productMatch[2]);
        let confidenceColor = "#ef4444";
        if (confidenceNum >= 80) confidenceColor = "#22c55e";
        else if (confidenceNum >= 60) confidenceColor = "#f59e0b";

        productsHtml += `
          <div style="display: flex; align-items: center; margin: 12px 0; padding: 16px; background: #f8fafc; border-radius: 8px; border-left: 4px solid ${confidenceColor};">
            <div style="margin-right: 16px; font-size: 18px; font-weight: bold; color: #4a5568; min-width: 40px;">
              ${index + 1}.
            </div>
            <div style="flex: 1; margin-right: 16px;">
              <div style="background: #e0f2fe; padding: 10px; border-radius: 6px; font-family: monospace; font-size: 14px; color: #0c4a6e;">
                ${escapeHtml(productMatch[1])}
              </div>
            </div>
            <div style="background: ${confidenceColor}; color: white; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold; min-width: 80px; text-align: center;">
              ${productMatch[2]}%
            </div>
          </div>
        `;
      });

      bodyHtml += withWhitePanel(
        '<h4 style="margin: 0 0 16px 0; color: #2d3748;">预测产物</h4>' +
          productsHtml,
      );
    }

    bodyHtml += `
      <div style="margin-top: 20px; padding: 16px; background: rgba(236, 72, 153, 0.1); border-radius: 8px; border-left: 4px solid #ec4899;">
        <p style="margin: 0; font-size: 14px; color: #4a5568;">
          <strong>说明：</strong> 预测结果按置信度排序，置信度越高表示反应越可能发生。
        </p>
      </div>
    `;

    return withCardFrame(
      "🧬",
      "反应预测结果",
      "IBM RXN for Chemistry",
      "linear-gradient(135deg, #fbb6ce20 0%, #f687b320 100%)",
      bodyHtml,
    );
  }

  function formatLiteratureResults(content) {
    if (!/(文献|专利|检索)/.test(content)) {
      return renderMarkdownLike(content);
    }

    const formattedContent = renderMarkdownLike(content)
      .replace(/📚/g, '<span style="font-size: 20px;">📚</span>')
      .replace(/🔬/g, '<span style="font-size: 20px;">🔬</span>')
      .replace(/📄/g, '<span style="font-size: 20px;">📄</span>');

    return withCardFrame(
      "📚",
      "文献数据检索结果",
      "IBM RXN for Chemistry 数据源",
      "linear-gradient(135deg, #a7f3d020 0%, #6ee7b720 100%)",
      withWhitePanel(formattedContent),
    );
  }

  function formatADMETResults(content) {
    if (!/(ADMET|理化性质|药代)/i.test(content)) {
      return renderMarkdownLike(content);
    }

    const formattedContent = renderMarkdownLike(content)
      .replace(
        /\*\*(.*?)\*\*/g,
        '<div style="background: #f7fafc; margin: 16px 0; padding: 12px; border-radius: 8px; border-left: 4px solid #4299e1;"><strong style="color: #1a365d; font-size: 16px;">$1</strong></div>',
      )
      .replace(
        /•\s*(.*?):\s*(.*?)$/gm,
        '<div style="margin: 8px 0; padding: 10px; background: white; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);"><span style="color: #4a5568; font-weight: 500;">$1:</span> <span style="color: #2d3748;">$2</span></div>',
      );

    return withCardFrame(
      "🩺",
      "ADMET 属性分析",
      "药代动力学与毒理学预测",
      "linear-gradient(135deg, #ffecd120 0%, #fed7aa20 100%)",
      withWhitePanel(formattedContent),
    );
  }

  function formatDrugLikenessResults(content) {
    if (!/(类药性|综合评估|Lipinski)/i.test(content)) {
      return renderMarkdownLike(content);
    }

    const formattedContent = renderMarkdownLike(content)
      .replace(
        /\*\*(.*?)\*\*/g,
        '<div style="background: #f8fafc; margin: 16px 0; padding: 12px; border-radius: 8px; border-left: 4px solid #8b5cf6;"><strong style="color: #1a202c; font-size: 16px;">$1</strong></div>',
      )
      .replace(
        /•\s*(.*?):\s*(.*?)$/gm,
        '<div style="margin: 8px 0; padding: 10px; background: white; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);"><span style="color: #4a5568; font-weight: 500;">$1:</span> <span style="color: #2d3748;">$2</span></div>',
      );

    return withCardFrame(
      "💊",
      "类药性评估",
      "药物相似性与成药性分析",
      "linear-gradient(135deg, #e0e7ff20 0%, #c7d2fe20 100%)",
      withWhitePanel(formattedContent),
    );
  }

  function formatMolecularProperties(content) {
    if (!/(基础分子属性|关键属性|molecular properties)/i.test(content)) {
      return renderMarkdownLike(content);
    }

    const formattedContent = renderMarkdownLike(content)
      .replace(
        /关键属性/g,
        '<strong style="color: #134e4a;">关键属性</strong>',
      )
      .replace(
        /•\s*(.*?):\s*(.*?)$/gm,
        '<div style="margin: 6px 0; padding: 8px 12px; background: white; border-radius: 6px; box-shadow: 0 1px 2px rgba(0,0,0,0.05);"><span style="color: #374151; font-weight: 500;">$1:</span> <span style="color: #1f2937;">$2</span></div>',
      );

    return withCardFrame(
      "🧫",
      "分子属性计算",
      "基础理化性质分析",
      "linear-gradient(135deg, #ecfdf520 0%, #d1fae520 100%)",
      withWhitePanel(formattedContent),
    );
  }

  function formatReActResults(content) {
    if (!/(推理过程|使用工具|分析结果)/.test(content)) {
      return renderMarkdownLike(content);
    }

    const formattedContent = renderMarkdownLike(content)
      .replace(
        /\*\*(推理过程|使用工具|分析结果):\*\*/g,
        '<div style="background: #fffbeb; margin: 16px 0; padding: 12px; border-radius: 8px; border-left: 4px solid #f59e0b;"><strong style="color: #92400e; font-size: 16px;">🧠 $1</strong></div>',
      )
      .replace(
        / {3}\d+\. (.+)/g,
        '<div style="margin: 8px 0 8px 20px; padding: 10px; background: white; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border-left: 3px solid #fbbf24;"><span style="color: #1f2937;">$1</span></div>',
      )
      .replace(
        / {6}→ 使用了 (.+?) 工具/g,
        '<div style="margin: 4px 0 4px 40px; padding: 6px 10px; background: #f3f4f6; border-radius: 4px; font-size: 13px; color: #6b7280;">🧠 使用了 <strong style="color: #374151;">$1</strong> 工具</div>',
      );

    return withCardFrame(
      "🧠",
      "智能推理分析",
      "ReAct 推理框架结果",
      "linear-gradient(135deg, #fef3c720 0%, #fde68a20 100%)",
      withWhitePanel(formattedContent),
    );
  }

  function formatContent(content) {
    if (typeof content === "string" && content) {
      content = content
        .replace(/```/g, "")
        .replace(/\*\*/g, "")
        .replace(/^\s*#{1,6}\s*/gm, "");
    }

    if (
      content.includes("逆合成分析结果") ||
      content.includes("合成路线") ||
      content.includes("步骤")
    ) {
      return formatSynthesisRoute(content);
    }
    if (
      content.includes("反应预测结果") ||
      content.includes("预测的可能产物")
    ) {
      return formatReactionPrediction(content);
    }
    if (content.includes("文献数据搜索结果") || content.includes("相关文献")) {
      return formatLiteratureResults(content);
    }
    if (
      content.includes("增强ADMET属性预测结果") ||
      content.includes("**理化性质:**")
    ) {
      return formatADMETResults(content);
    }
    if (
      content.includes("类药性评估结果") ||
      content.includes("**综合评估:**")
    ) {
      return formatDrugLikenessResults(content);
    }
    if (
      content.includes("基础分子属性计算结果") ||
      content.includes("关键属性:")
    ) {
      return formatMolecularProperties(content);
    }
    if (
      content.includes("**推理过程:**") ||
      content.includes("**使用工具:**")
    ) {
      return formatReActResults(content);
    }

    return renderMarkdownLike(content);
  }

  return {
    formatContent: formatContent,
    formatSynthesisRoute: formatSynthesisRoute,
    formatReactionPrediction: formatReactionPrediction,
    formatLiteratureResults: formatLiteratureResults,
    formatADMETResults: formatADMETResults,
    formatDrugLikenessResults: formatDrugLikenessResults,
    formatMolecularProperties: formatMolecularProperties,
    formatReActResults: formatReActResults,
  };
})();
