# Handoff contracts

These examples explain the portable contracts. The JSON Schema files are canonical and reject unknown fields:

- [presentation intent schema](schemas/aippt-presentation-intent-v1.schema.json)
- [expression review schema](schemas/aippt-expression-review-v1.schema.json)
- [learning packet schema](schemas/aippt-learning-packet-v1.schema.json)
- [bridge capabilities schema](schemas/aippt-bridge-capabilities-v1.schema.json)
- [guidance snapshot schema](schemas/aippt-guidance-snapshot-v1.schema.json)
- [self-contained handoff bundle schema](schemas/aippt-handoff-bundle-v1.schema.json)

Every v1 PowerPoint handoff is a `source-side report`. Its outer bundle and inner learning packet both set `claim_provenance` to `source_report_unverified`. JSON Schema checks shape; the receiving Studio importer separately checks that embedded identifiers, revision bindings, and transfer scopes agree.

A source packet has only two `transmission_state` values: `prepared_not_transmitted` and `transmitted`. It has no receiver lifecycle field, receiver receipt, validation/rejection state, attachment record, or attachment-transfer field. The local importer may later create its own `received_external_unverified` receipt after it accepts exact bundle bytes. That independent local receipt does not make source claims verified.

Use opaque host identifiers and concise visible evidence. Never put a local path, raw conversation, credential, PPTX bytes, speaker notes, PDF/Word body, image bytes, or connected-app export into a contract. State unavailable, not_attempted, not_performed, not_comparable, or unknown when that is the true host condition.

## Presentation intent

Create this before editing or composing. It distinguishes a narrow correction from a template derivative, a new composition, a faithful reconstruction, or a visual alternate.

~~~json
{
  "schema": "aippt-presentation-intent-v1",
  "intent_id": "intent-001",
  "created_at": "2026-09-22T00:00:00+00:00",
  "source_revision": {
    "document_id": "host-document-77",
    "revision": "revision-4",
    "state": "observed",
    "evidence": [
      "host-visible presentation state"
    ]
  },
  "operation": "exact_local_revision",
  "presentation_form": "business_report",
  "audience": "management meeting",
  "occasion": "quarterly review",
  "source_boundary": {
    "authoritative_material": [
      "active deck",
      "current source file"
    ],
    "may_add_external_material": false,
    "external_material_authorization": "not_authorized"
  },
  "scope": {
    "target_slides": [
      "3"
    ],
    "preserve": [
      "accepted slides",
      "existing navigation"
    ],
    "unknowns": [],
    "preservation_basis": "current_request"
  },
  "page_messages": [
    {
      "page": "3",
      "message": "The comparison explains the operating change.",
      "evidence_role": "comparison chart and source-backed annotation",
      "neighbor_logic": "It follows the problem and prepares the implementation page."
    }
  ],
  "capability_limits": []
}
~~~

## Capability record

Create this before claiming that an operation happened. Each state is about one operation, not the host in general. `transmit_handoff` is required even for a manual bundle; use `not_attempted` unless an authorized direct handoff action actually ran.

~~~json
{
  "schema": "aippt-bridge-capabilities-v1",
  "report_id": "capabilities-001",
  "created_at": "2026-09-22T00:00:00+00:00",
  "source_surface": "chatgpt-powerpoint",
  "source_revision": {
    "document_id": "host-document-77",
    "revision": "revision-4",
    "state": "observed",
    "evidence": [
      "host-visible presentation state"
    ]
  },
  "operations": {
    "read_active_slide": {
      "state": "confirmed",
      "evidence": [
        "active slide content visible"
      ]
    },
    "edit_text": {
      "state": "confirmed",
      "evidence": [
        "post-edit text visible"
      ]
    },
    "edit_geometry": {
      "state": "unknown",
      "evidence": []
    },
    "render_page": {
      "state": "confirmed",
      "evidence": [
        "host page preview"
      ]
    },
    "save_reopen_pptx": {
      "state": "not_attempted",
      "evidence": []
    },
    "export_or_inspect_pdf": {
      "state": "unavailable",
      "evidence": [
        "export control not exposed"
      ]
    },
    "animation_playback": {
      "state": "not_attempted",
      "evidence": []
    },
    "transmit_handoff": {
      "state": "not_attempted",
      "evidence": []
    }
  },
  "bridge_mode": "manual_packet",
  "connected_apps": [],
  "limitations": [
    "No approved handoff action is visible in this host."
  ]
}
~~~

## Expression review

Bind review observations to a document revision. A visible change after review invalidates it when it changes copy, geometry, image crop or mask, theme or master, source material, or host save/reopen state.

~~~json
{
  "schema": "aippt-expression-review-v1",
  "review_id": "review-001",
  "created_at": "2026-09-22T00:00:00+00:00",
  "document_revision": {
    "document_id": "host-document-77",
    "pre_review_revision": "revision-3",
    "reviewed_revision": "revision-4",
    "binding_state": "bound_to_observed_revision",
    "evidence": [
      "host preview after edit"
    ]
  },
  "review_scope": [
    "slide 3"
  ],
  "visual_inspection": {
    "state": "performed",
    "evidence": [
      "active-slide preview"
    ]
  },
  "observations": [
    {
      "scope": "slide 3 image and supporting text",
      "visible_observation": "The original rectangular image edge was exposed beyond the rounded visual frame.",
      "message_effect": "The mismatch distracted from the evidence comparison.",
      "decision": "revised",
      "revision_or_reason": "The visible image crop now follows the intended rounded edge.",
      "evidence": [
        "post-edit active-slide preview"
      ]
    }
  ],
  "adjacent_page_check": {
    "state": "performed",
    "result": "The revised comparison follows the problem page and prepares the next action page.",
    "evidence": [
      "three-slide overview"
    ]
  },
  "preservation_check": {
    "state": "not_comparable",
    "evidence": [
      "host exposes no before-and-after non-target comparison"
    ],
    "non_target_slides": {
      "state": "not_comparable",
      "result": "Preservation intent was followed, but non-target pages could not be independently compared.",
      "evidence": [
        "host limitation"
      ]
    }
  },
  "invalidated_by_changes": [
    "none"
  ],
  "review_state": "reviewed_current_revision",
  "unverified_limits": [
    "Native save and reopen were not tested."
  ]
}
~~~

## Learning packet

The normal manual packet uses `prepared_not_transmitted` and an empty `transmission_evidence` list. It cannot claim receiver arrival, external validation, rejection, or any content transfer. A packet does not prove local raw-conversation provenance, user acceptance, tool execution, active guidance, or a core repair.

~~~json
{
  "schema": "aippt-learning-packet-v1",
  "packet_id": "office-packet-001",
  "created_at": "2026-09-22T00:00:00+00:00",
  "source_surface": "chatgpt-powerpoint",
  "claim_provenance": "source_report_unverified",
  "source_revision": {
    "document_id": "host-document-77",
    "revision": "revision-4",
    "state": "observed",
    "evidence": [
      "host-visible presentation state"
    ]
  },
  "transfer_scope": {
    "mode": "sanitized_structured_summary_only",
    "deck_content_embedded": false,
    "speaker_notes_embedded": false,
    "asset_content_embedded": false,
    "raw_conversation_embedded": false
  },
  "user_authorization": {
    "state": "recorded",
    "scope": "structured_summary_only",
    "evidence": [
      "current-task handoff request"
    ]
  },
  "idempotency_key": "office-packet-001-revision-4",
  "transmission_state": "prepared_not_transmitted",
  "transmission_evidence": [],
  "presentation_intent_ref": "intent-001",
  "expression_review_ref": "review-001",
  "capability_report_ref": "capabilities-001",
  "feedback_events": [
    {
      "event_id": "feedback-001",
      "kind": "feedback",
      "user_observation": "The photo edge conflicts with the rounded visual frame.",
      "context": {
        "operation": "exact_local_revision",
        "presentation_form": "business_report",
        "scope": "slide 3 image"
      },
      "problem": "The visible image retained exposed rectangular corners.",
      "correction": "Applied a visible crop matching the rounded frame.",
      "verification": {
        "state": "visually_reviewed",
        "evidence": [
          "active-slide preview"
        ]
      },
      "reusability": {
        "state": "conditional_candidate",
        "applies_when": "A photo sits in a curved visible frame.",
        "exceptions": [
          "A rectangular source edge may be intentional evidence."
        ]
      }
    }
  ],
  "tool_execution": {
    "state": "not_reported",
    "evidence": []
  },
  "artifact_review": {
    "state": "performed",
    "evidence": [
      "active-slide preview"
    ]
  },
  "user_acceptance": {
    "state": "not_recorded",
    "verification": "not_verified",
    "evidence": []
  },
  "unresolved_items": [
    "Native export was unavailable in the current host."
  ]
}
~~~

If a current task separately supplies an `aippt-guidance-snapshot-v1`, add both `guidance_snapshot_ref` and `guidance_dispositions` to the packet; otherwise omit both. The snapshot is read-only and remains outside this self-contained source bundle.

Use `transmitted` only when all of the following are true:

1. the current user authorized the sanitized structured-summary transfer and that authorization has source evidence;
2. `transmission_evidence` records the source-side send attempt;
3. the embedded capability report has `bridge_mode` `direct_bridge`, a visible approved bridge, and `operations.transmit_handoff.state` `confirmed` with evidence.

Even then, `transmitted` is only a source-side send-attempt assertion. It does not prove arrival or acceptance, and no receiver receipt belongs in the v1 source packet.

## Self-contained manual handoff bundle

For manual local import, emit one `aippt-handoff-bundle-v1`, not only the learning packet. It requires `bundle_id`, `created_at`, `source_surface`, `claim_provenance`, `source_revision`, `transfer_scope`, `packet`, `presentation_intent`, and `capability_report`. Include `expression_review` exactly when the inner packet has `expression_review_ref`.

The bundle repeats the same opaque source revision and transfer scope as its inner packet, embeds complete intent/capability/review records rather than local-only references, and requires the packet references to match embedded record identifiers. It remains a structured summary: never embed deck or file bytes, notes, raw conversation, private paths, credentials, source inventories, or app exports. The normal manual bundle below is prepared but not transmitted.

~~~json
{
  "schema": "aippt-handoff-bundle-v1",
  "bundle_id": "office-bundle-001",
  "created_at": "2026-09-22T00:00:00+00:00",
  "source_surface": "chatgpt-powerpoint",
  "claim_provenance": "source_report_unverified",
  "source_revision": {
    "document_id": "host-document-77",
    "revision": "revision-4",
    "state": "observed",
    "evidence": [
      "host-visible presentation state"
    ]
  },
  "transfer_scope": {
    "mode": "sanitized_structured_summary_only",
    "deck_content_embedded": false,
    "speaker_notes_embedded": false,
    "asset_content_embedded": false,
    "raw_conversation_embedded": false
  },
  "packet": {
    "schema": "aippt-learning-packet-v1",
    "packet_id": "office-packet-001",
    "created_at": "2026-09-22T00:00:00+00:00",
    "source_surface": "chatgpt-powerpoint",
    "claim_provenance": "source_report_unverified",
    "source_revision": {
      "document_id": "host-document-77",
      "revision": "revision-4",
      "state": "observed",
      "evidence": [
        "host-visible presentation state"
      ]
    },
    "transfer_scope": {
      "mode": "sanitized_structured_summary_only",
      "deck_content_embedded": false,
      "speaker_notes_embedded": false,
      "asset_content_embedded": false,
      "raw_conversation_embedded": false
    },
    "user_authorization": {
      "state": "recorded",
      "scope": "structured_summary_only",
      "evidence": [
        "current-task handoff request"
      ]
    },
    "idempotency_key": "office-packet-001-revision-4",
    "transmission_state": "prepared_not_transmitted",
    "transmission_evidence": [],
    "presentation_intent_ref": "intent-001",
    "expression_review_ref": "review-001",
    "capability_report_ref": "capabilities-001",
    "feedback_events": [
      {
        "event_id": "feedback-001",
        "kind": "feedback",
        "user_observation": "The photo edge conflicts with the rounded visual frame.",
        "context": {
          "operation": "exact_local_revision",
          "presentation_form": "business_report",
          "scope": "slide 3 image"
        },
        "problem": "The visible image retained exposed rectangular corners.",
        "correction": "Applied a visible crop matching the rounded frame.",
        "verification": {
          "state": "visually_reviewed",
          "evidence": [
            "active-slide preview"
          ]
        },
        "reusability": {
          "state": "conditional_candidate",
          "applies_when": "A photo sits in a curved visible frame.",
          "exceptions": [
            "A rectangular source edge may be intentional evidence."
          ]
        }
      }
    ],
    "tool_execution": {
      "state": "not_reported",
      "evidence": []
    },
    "artifact_review": {
      "state": "performed",
      "evidence": [
        "active-slide preview"
      ]
    },
    "user_acceptance": {
      "state": "not_recorded",
      "verification": "not_verified",
      "evidence": []
    },
    "unresolved_items": [
      "Native export was unavailable in the current host."
    ]
  },
  "presentation_intent": {
    "schema": "aippt-presentation-intent-v1",
    "intent_id": "intent-001",
    "created_at": "2026-09-22T00:00:00+00:00",
    "source_revision": {
      "document_id": "host-document-77",
      "revision": "revision-4",
      "state": "observed",
      "evidence": [
        "host-visible presentation state"
      ]
    },
    "operation": "exact_local_revision",
    "presentation_form": "business_report",
    "audience": "management meeting",
    "occasion": "quarterly review",
    "source_boundary": {
      "authoritative_material": [
        "active deck",
        "current source file"
      ],
      "may_add_external_material": false,
      "external_material_authorization": "not_authorized"
    },
    "scope": {
      "target_slides": [
        "3"
      ],
      "preserve": [
        "accepted slides",
        "existing navigation"
      ],
      "unknowns": [],
      "preservation_basis": "current_request"
    },
    "page_messages": [
      {
        "page": "3",
        "message": "The comparison explains the operating change.",
        "evidence_role": "comparison chart and source-backed annotation",
        "neighbor_logic": "It follows the problem and prepares the implementation page."
      }
    ],
    "capability_limits": []
  },
  "expression_review": {
    "schema": "aippt-expression-review-v1",
    "review_id": "review-001",
    "created_at": "2026-09-22T00:00:00+00:00",
    "document_revision": {
      "document_id": "host-document-77",
      "pre_review_revision": "revision-3",
      "reviewed_revision": "revision-4",
      "binding_state": "bound_to_observed_revision",
      "evidence": [
        "host preview after edit"
      ]
    },
    "review_scope": [
      "slide 3"
    ],
    "visual_inspection": {
      "state": "performed",
      "evidence": [
        "active-slide preview"
      ]
    },
    "observations": [
      {
        "scope": "slide 3 image and supporting text",
        "visible_observation": "The original rectangular image edge was exposed beyond the rounded visual frame.",
        "message_effect": "The mismatch distracted from the evidence comparison.",
        "decision": "revised",
        "revision_or_reason": "The visible image crop now follows the intended rounded edge.",
        "evidence": [
          "post-edit active-slide preview"
        ]
      }
    ],
    "adjacent_page_check": {
      "state": "performed",
      "result": "The revised comparison follows the problem page and prepares the next action page.",
      "evidence": [
        "three-slide overview"
      ]
    },
    "preservation_check": {
      "state": "not_comparable",
      "evidence": [
        "host exposes no before-and-after non-target comparison"
      ],
      "non_target_slides": {
        "state": "not_comparable",
        "result": "Preservation intent was followed, but non-target pages could not be independently compared.",
        "evidence": [
          "host limitation"
        ]
      }
    },
    "invalidated_by_changes": [
      "none"
    ],
    "review_state": "reviewed_current_revision",
    "unverified_limits": [
      "Native save and reopen were not tested."
    ]
  },
  "capability_report": {
    "schema": "aippt-bridge-capabilities-v1",
    "report_id": "capabilities-001",
    "created_at": "2026-09-22T00:00:00+00:00",
    "source_surface": "chatgpt-powerpoint",
    "source_revision": {
      "document_id": "host-document-77",
      "revision": "revision-4",
      "state": "observed",
      "evidence": [
        "host-visible presentation state"
      ]
    },
    "operations": {
      "read_active_slide": {
        "state": "confirmed",
        "evidence": [
          "active slide content visible"
        ]
      },
      "edit_text": {
        "state": "confirmed",
        "evidence": [
          "post-edit text visible"
        ]
      },
      "edit_geometry": {
        "state": "unknown",
        "evidence": []
      },
      "render_page": {
        "state": "confirmed",
        "evidence": [
          "host page preview"
        ]
      },
      "save_reopen_pptx": {
        "state": "not_attempted",
        "evidence": []
      },
      "export_or_inspect_pdf": {
        "state": "unavailable",
        "evidence": [
          "export control not exposed"
        ]
      },
      "animation_playback": {
        "state": "not_attempted",
        "evidence": []
      },
      "transmit_handoff": {
        "state": "not_attempted",
        "evidence": []
      }
    },
    "bridge_mode": "manual_packet",
    "connected_apps": [],
    "limitations": [
      "No approved handoff action is visible in this host."
    ]
  }
}
~~~

The local `packet-import` command freezes the exact bundle bytes and writes a separate local receipt. The source bundle cannot include that receipt or claim that the importer accepted any assertion. The local receipt starts as `received_external_unverified`; a current local task must still corroborate any observation before it enters the evidence-backed learning path.

## Guidance snapshot

A local Studio can export an `aippt-guidance-snapshot-v1` as a de-identified, read-only companion file. A user may provide it for the current Office task. It is never an automatic history sync or a source-bundle receiver response.

`learned_projection` says whether this snapshot contains only requested base rules, includes a finite verified generic projection, or withholds learned projection because integrity is not ready or no generic projection is available. It never serializes free-form historical feedback, customer material, local paths, or raw archive text.

~~~json
{
  "schema": "aippt-guidance-snapshot-v1",
  "snapshot_id": "guidance-001",
  "created_at": "2026-09-22T00:00:00+00:00",
  "source_workbench": "aippt-studio",
  "guidance_revision": "v1-0123456789abcdef",
  "transfer_scope": "sanitized_active_guidance_only",
  "authorization": {
    "state": "user_provided_current_task",
    "evidence": [
      "current-task file supplied by user"
    ]
  },
  "status": "active_read_only",
  "learned_projection": "base_only_requested",
  "rules": [
    {
      "rule_id": "base:image-surface-geometry",
      "status": "active",
      "action": "Review the visible image edge and its mask together.",
      "applies_when": "A photo sits on a curved or rounded visual frame.",
      "exceptions": [
        "The original rectangular edge is intentional evidence."
      ]
    }
  ],
  "limitations": [
    "The snapshot does not prove a native edit, visual review, delivery, user acceptance, or core repair."
  ]
}
~~~
