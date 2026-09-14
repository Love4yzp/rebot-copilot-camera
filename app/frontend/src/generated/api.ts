export interface paths {
    "/api/estop": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Estop */
        get: operations["get_estop_api_estop_get"];
        put?: never;
        /**
         * Engage Estop
         * @description Engage the stop.
         *
         *     Always 200, never 409: an emergency stop that argues with you is a broken
         *     emergency stop. Re-engaging an already-latched stop is a no-op that keeps
         *     the original reason, reported via ``changed: false``.
         */
        post: operations["engage_estop_api_estop_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/estop/clear": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Clear Estop
         * @description Release the stop. The arm stays holding; nothing resumes.
         *
         *     Teaching is a separate intent. Auto-entering drag after a clear made
         *     uncalibrated gravity feedforward look like a safety recovery.
         *
         *     Deliberately not behind the motion gate -- gating the escape hatch on the
         *     thing it escapes would wedge the system.
         */
        post: operations["clear_estop_api_estop_clear_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/poses": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Poses */
        get: operations["list_poses_api_poses_get"];
        put?: never;
        /** Create Pose */
        post: operations["create_pose_api_poses_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/poses/capture": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Capture Pose
         * @description Record wherever the arm is standing right now, under a name.
         *
         *     This is the "press the button" half of drag teaching: the operator has
         *     already positioned the arm by hand and let go. Not behind the motion gate —
         *     it reads a pose and writes a record, and an operator who has just stopped
         *     the arm may well want the pose it stopped at.
         */
        post: operations["capture_pose_api_poses_capture_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/poses/{pose_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /**
         * Delete Pose
         * @description Delete directly: telling the operator what this pose feeds first is the
         *     UI's job (it asked GET links before offering the button).
         */
        delete: operations["delete_pose_api_poses__pose_id__delete"];
        options?: never;
        head?: never;
        /** Patch Pose */
        patch: operations["patch_pose_api_poses__pose_id__patch"];
        trace?: never;
    };
    "/api/poses/{pose_id}/links": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Pose Links
         * @description Which sequences link this pose, reported before delete/overwrite —
         *     silently rewriting the physical path of N sequences is the "a whole round
         *     of empty frames" class of failure.
         */
        get: operations["pose_links_api_poses__pose_id__links_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/poses/{pose_id}/goto": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Goto Pose
         * @description Move to one pose and stay there — the library card's "去这里".
         */
        post: operations["goto_pose_api_poses__pose_id__goto_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sequences": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Sequences */
        get: operations["list_sequences_api_sequences_get"];
        put?: never;
        /** Create Sequence */
        post: operations["create_sequence_api_sequences_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sequences/{sid}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Sequence */
        get: operations["get_sequence_api_sequences__sid__get"];
        put?: never;
        post?: never;
        /** Delete Sequence */
        delete: operations["delete_sequence_api_sequences__sid__delete"];
        options?: never;
        head?: never;
        /** Patch Sequence */
        patch: operations["patch_sequence_api_sequences__sid__patch"];
        trace?: never;
    };
    "/api/sequences/{sid}/execute": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Execute Sequence
         * @description Run the sequence for real — the arm moves.
         */
        post: operations["execute_sequence_api_sequences__sid__execute_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/templates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Templates */
        get: operations["list_templates_api_templates_get"];
        put?: never;
        /**
         * Create Template
         * @description Snapshot a sequence as a structural recipe (pose slots, no joints).
         */
        post: operations["create_template_api_templates_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/templates/{tid}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Template */
        delete: operations["delete_template_api_templates__tid__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/templates/{tid}/instantiate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Instantiate Template
         * @description Copy the recipe with each slot bound to a library pose.
         */
        post: operations["instantiate_template_api_templates__tid__instantiate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/control": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Control State */
        get: operations["get_control_state_api_control_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/execute/stop": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Stop Execution
         * @description Stop the run. Not gated: stopping must work while stopped.
         */
        post: operations["stop_execution_api_execute_stop_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/execute/resume": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Resume Execution
         * @description Continue past the wait marker the run is suspended on.
         *
         *     Gated: resuming is motion. A stop engaged during the wait already aborted
         *     the run, so by the time the gate passes there is usually nothing to resume.
         */
        post: operations["resume_execution_api_execute_resume_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/teach": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Set Teaching */
        post: operations["set_teaching_api_teach_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/rest": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Set Resting
         * @description Rest: drop torque at the zero pose — the arm lies on its stops and the
         *     motors stop burning current. Gated: resting changes what the motors are
         *     commanded, and waking re-asserts a hold.
         */
        post: operations["set_resting_api_rest_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/logs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Logs
         * @description Recent service log lines, newest last.
         */
        get: operations["get_logs_api_logs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/config/tuning": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Tuning */
        get: operations["get_tuning_api_config_tuning_get"];
        /**
         * Put Tuning
         * @description Hot-apply a partial patch. Validated as the *merged* config, so a
         *     camera mass and the camera profile may arrive in either order.
         */
        put: operations["put_tuning_api_config_tuning_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/config/tuning/save": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Save Tuning
         * @description Persist the live config. Explicit on purpose: hot-applied values die
         *     with the process unless the operator says keep them.
         */
        post: operations["save_tuning_api_config_tuning_save_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/config/tuning/reset": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Reset Tuning
         * @description Reload the saved file and apply it. This is the "give me back what I
         *     had before I started fiddling" button, not a factory reset.
         */
        post: operations["reset_tuning_api_config_tuning_reset_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sim/perturb": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Perturb */
        post: operations["perturb_api_sim_perturb_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sim/state": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** State */
        get: operations["state_api_sim_state_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/viewer/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Page */
        get: operations["page_viewer__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/viewer/main.min.js": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Script */
        get: operations["script_viewer_main_min_js_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Health
         * @description Liveness plus enough identity to tell two deployments apart.
         */
        get: operations["health_api_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * ApproachTuning
         * @description Ceiling on joint speed for the first approach (and goto), rad/s.
         */
        ApproachTuning: {
            /**
             * First Max Speed
             * @default 0.25
             */
            first_max_speed: number;
        };
        /**
         * CameraPayload
         * @description The camera + mount as an equivalent point mass on the end link.
         *
         *     ``com`` is expressed in the ``gripper_end`` link frame — the camera
         *     mounts where the gripper did. Only mass and centre of mass feed the
         *     gravity model; the inertia tensor is left as the link's own (gravity
         *     feedforward does not read it), so this is honest about being a
         *     gravity-only calibration.
         */
        CameraPayload: {
            /** Mass */
            mass?: number | null;
            /**
             * Com
             * @default [
             *       0,
             *       0,
             *       0
             *     ]
             */
            com: [
                number,
                number,
                number
            ];
        };
        /** CapturePose */
        CapturePose: {
            /**
             * Name
             * @default
             */
            name: string;
        };
        /** CreatePose */
        CreatePose: {
            /**
             * Name
             * @default
             */
            name: string;
            /** Joints */
            joints?: {
                [key: string]: number;
            } | null;
        };
        /** CreateSequence */
        CreateSequence: {
            /**
             * Name
             * @default
             */
            name: string;
        };
        /** CreateTemplate */
        CreateTemplate: {
            /**
             * Sequence Id
             * @default
             */
            sequence_id: string;
            /** Name */
            name?: string | null;
        };
        /** EngageRequest */
        EngageRequest: {
            /**
             * Reason
             * @description Shown to whoever has to work out why the arm stopped.
             * @default operator engaged emergency stop
             */
            reason: string;
            /** @default api */
            source: components["schemas"]["LatchSource"];
        };
        /** EstopStatus */
        EstopStatus: {
            /** Latched */
            latched: boolean;
            /** Reason */
            reason?: string | null;
            source?: components["schemas"]["LatchSource"] | null;
            /** Engaged At */
            engaged_at?: number | null;
            /** Freeze Pose */
            freeze_pose?: {
                [key: string]: number;
            } | null;
            /** Changed */
            changed?: boolean | null;
        };
        /**
         * EventMarker
         * @description An action pinned inside its parent block, at a time position inside it.
         *
         *     Inside a hold ``at`` is a second offset (0..duration_s); inside a
         *     transition it is a proportion (0..1) — splitting a transition to say
         *     "midway" would invent a pose nobody taught.
         */
        EventMarker: {
            /** Id */
            id?: string;
            /**
             * Kind
             * @default wait
             * @constant
             */
            kind: "wait";
            /** Params */
            params?: {
                [key: string]: unknown;
            };
            /** At */
            at: number;
            /**
             * Estimate S
             * @default 0
             * @constant
             */
            estimate_s: 0;
        };
        /** FloatLockTuning */
        FloatLockTuning: {
            /**
             * Linear Threshold
             * @default 0.04
             */
            linear_threshold: number;
            /**
             * Angular Threshold
             * @default 0.08
             */
            angular_threshold: number;
            /**
             * Release Factor
             * @default 1
             */
            release_factor: number;
            /**
             * Lock Factor
             * @default 0.6
             */
            lock_factor: number;
            /**
             * Min Still S
             * @default 0.25
             */
            min_still_s: number;
        };
        /**
         * FloatTuning
         * @description MIT gains while floating (kp near zero = your hand moves the arm).
         */
        FloatTuning: {
            /**
             * Kp
             * @default 2
             */
            kp: number;
            /**
             * Kd
             * @default 1
             */
            kd: number;
        };
        /**
         * GravityTuning
         * @description Per-joint correction of the gravity feedforward, mirroring upstream's
         *     ``auto_float_test`` ``--k/--c`` knobs:
         *
         *         tau_sent = scale[joint] * g_model(q)[joint] + bias[joint]
         *
         *     Missing joints are identity (1.0 / 0.0). This is the operator's lever for
         *     the vendor model's residual error — the j2 over-compensation that floats
         *     the arm up at extended poses is corrected by a scale below 1 on joint2.
         *     Only the six arm joints are legal keys; the gripper has no calibrated
         *     mapping and stays at zero feedforward.
         */
        GravityTuning: {
            /** Scale */
            scale?: {
                [key: string]: number;
            };
            /** Bias */
            bias?: {
                [key: string]: number;
            };
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * HoldBlock
         * @description Stay at a library pose for ``duration_s``. The station.
         */
        HoldBlock: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "hold";
            /** Id */
            id?: string;
            /** Pose Id */
            pose_id: string;
            /**
             * Duration S
             * @default 3
             */
            duration_s: number;
            /** Markers */
            markers?: components["schemas"]["EventMarker"][];
        };
        /** InstantiateTemplate */
        InstantiateTemplate: {
            /**
             * Name
             * @default
             */
            name: string;
            /**
             * Pose Ids
             * @default []
             */
            pose_ids: string[];
        };
        /**
         * LatchSource
         * @description Who engaged the latch. Kept in the snapshot for the operator's benefit.
         * @enum {string}
         */
        LatchSource: "ui" | "api" | "watchdog";
        /** LogResponse */
        LogResponse: {
            /** Available */
            available: boolean;
            /** Lines */
            lines: string[];
            /** Note */
            note?: string | null;
        };
        /**
         * PatchPose
         * @description Every field optional — omitted means unchanged.
         */
        PatchPose: {
            /** Name */
            name?: string | null;
            /** Joints */
            joints?: {
                [key: string]: number;
            } | null;
        };
        /**
         * PatchSequence
         * @description Every field optional — omitted means unchanged. ``blocks`` is a
         *     whole-document replace; the server normalizes before storing.
         */
        PatchSequence: {
            /** Name */
            name?: string | null;
            /** Blocks */
            blocks?: (components["schemas"]["HoldBlock"] | components["schemas"]["TransitionBlock"])[] | null;
        };
        /**
         * PayloadProfile
         * @description What *mass* hangs off the end flange — orthogonal to the hardware yaml's
         *     ``gripper`` switch, which answers a different question: whether the
         *     gripper *motor* is on the bus.
         *
         *     ``gripper`` means the gripper assembly's mass hangs off the end, motor
         *     wired or not. With the motor off the bus it is dead weight: the dynamics
         *     model carries its mass (it is physically there) while the actuator is
         *     absent. With the motor on, this is the only legal profile — a wired motor
         *     cannot be hot-added.
         *
         *     ``bare`` and ``camera`` are motor-less either way; they differ only in
         *     what the gravity model carries.
         * @enum {string}
         */
        PayloadProfile: "bare" | "camera" | "gripper";
        /** PayloadTuning */
        PayloadTuning: {
            /** @default bare */
            profile: components["schemas"]["PayloadProfile"];
            camera?: components["schemas"]["CameraPayload"];
        };
        /** Perturbation */
        Perturbation: {
            /** Joint */
            joint: string;
            /** Torque */
            torque: number;
            /**
             * Duration S
             * @default 0.15
             */
            duration_s: number;
        };
        /** PlaybackState */
        PlaybackState: {
            /** Mode */
            mode: string;
            /** Activity */
            activity: string;
            /** Playing */
            playing: boolean;
            /** Teaching */
            teaching: boolean;
            /** Rate Hz */
            rate_hz: number;
            /** Playback */
            playback?: {
                [key: string]: unknown;
            } | null;
            /** Source */
            source?: string | null;
        };
        /**
         * Pose
         * @description A named arm pose in the library. Hold blocks link to it by id.
         *
         *     Joint *names* and joint *limits* are deliberately not validated here, same
         *     as the old Waypoint: limits come from the URDF, this model only guarantees
         *     the shape is sane.
         */
        Pose: {
            /** Id */
            id?: string;
            /** Name */
            name: string;
            /** Joints */
            joints: {
                [key: string]: number;
            };
            /** Created At */
            created_at?: number;
            /** Updated At */
            updated_at?: number;
        };
        /** PoseLink */
        PoseLink: {
            /** Sequence Id */
            sequence_id: string;
            /** Sequence Name */
            sequence_name: string;
            /** Block Count */
            block_count: number;
        };
        /** PoseLinks */
        PoseLinks: {
            /** Pose Id */
            pose_id: string;
            /** Count */
            count: number;
            /** Links */
            links: components["schemas"]["PoseLink"][];
        };
        /** RestRequest */
        RestRequest: {
            /** Enabled */
            enabled: boolean;
        };
        /**
         * SeqTemplate
         * @description A structural recipe: blocks with each hold's pose_id replaced by a slot
         *     placeholder ("slot:1".."slot:N"). No joint angles — a template's value is
         *     the structure, and angles taught in one studio are wrong in another.
         */
        SeqTemplate: {
            /** Id */
            id?: string;
            /** Name */
            name: string;
            /** Created At */
            created_at?: number;
            /** Station Count */
            station_count: number;
            /** Recipe */
            recipe?: (components["schemas"]["HoldBlock"] | components["schemas"]["TransitionBlock"])[];
        };
        /**
         * Sequence
         * @description An ordered list of blocks — one execution.
         */
        Sequence: {
            /**
             * Schema Version
             * @default 3
             * @constant
             */
            schema_version: 3;
            /** Id */
            id?: string;
            /** Name */
            name: string;
            /** Created At */
            created_at?: number;
            /** Updated At */
            updated_at?: number;
            /** Blocks */
            blocks?: (components["schemas"]["HoldBlock"] | components["schemas"]["TransitionBlock"])[];
        };
        /**
         * SequenceSummary
         * @description Enough to render the library list without loading every block.
         *
         *     ``duration_s`` is the plan-ruler length: the sum of *commanded* durations.
         *     Markers add nothing — their durations are estimates and a wait marker is
         *     open-ended, so the UI always labels this number 预估.
         */
        SequenceSummary: {
            /** Id */
            id: string;
            /** Name */
            name: string;
            /** Updated At */
            updated_at: number;
            /** Station Count */
            station_count: number;
            /** Duration S */
            duration_s: number;
        };
        /**
         * SettleTuning
         * @description "Arrived" = inside the eps window *and* drifting less than this.
         */
        SettleTuning: {
            /**
             * Drift Rad
             * @default 0.003
             */
            drift_rad: number;
            /**
             * Min S
             * @default 0.15
             */
            min_s: number;
        };
        /** SimulationState */
        SimulationState: {
            /** Payload */
            payload: string;
            /** Inertia Source */
            inertia_source: string;
            /** Model Sha256 */
            model_sha256: string;
            /** Model Source */
            model_source: string;
            /** Wall Lag S */
            wall_lag_s: number;
            /** T */
            t?: number | null;
            /** Q */
            q?: number[] | null;
            /** V */
            v?: number[] | null;
            /** Tau */
            tau?: number[] | null;
            /** Error */
            error?: number[] | null;
            /** Saturated */
            saturated?: boolean[] | null;
            /** Ncon */
            ncon?: number | null;
        };
        /** TeachRequest */
        TeachRequest: {
            /** Enabled */
            enabled: boolean;
        };
        /**
         * TransitionBlock
         * @description Get to the next station's pose over ``duration_s``.
         *
         *     Generated by normalization, never edited into existence by hand: two
         *     different poses adjacent means the arm must physically get there, and that
         *     is not a setting.
         */
        TransitionBlock: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "transition";
            /** Id */
            id?: string;
            /**
             * Duration S
             * @default 2
             */
            duration_s: number;
            /**
             * Easing
             * @default ease_in_out
             * @enum {string}
             */
            easing: "linear" | "ease_in" | "ease_out" | "ease_in_out";
            /** Markers */
            markers?: components["schemas"]["EventMarker"][];
        };
        /** TriggerRequest */
        TriggerRequest: {
            /**
             * Source
             * @description Who is triggering this: the UI, an agent, a foot switch, a shot-list script. Recorded and broadcast so that 'why did the arm move' has an answer; it grants nothing and changes no motion.
             * @default ui
             */
            source: string;
        };
        /** TuningConfig */
        TuningConfig: {
            payload?: components["schemas"]["PayloadTuning"];
            float?: components["schemas"]["FloatTuning"];
            floatlock?: components["schemas"]["FloatLockTuning"];
            settle?: components["schemas"]["SettleTuning"];
            approach?: components["schemas"]["ApproachTuning"];
            gravity?: components["schemas"]["GravityTuning"];
        };
        /** TuningState */
        TuningState: {
            current: components["schemas"]["TuningConfig"];
            saved: components["schemas"]["TuningConfig"];
            /** Dirty */
            dirty?: string[];
            /** Gripper Motor */
            gripper_motor: boolean;
            /** Payload Options */
            payload_options: string[];
            /**
             * Model Locked
             * @default false
             */
            model_locked: boolean;
        };
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
            /** Input */
            input?: unknown;
            /** Context */
            ctx?: Record<string, never>;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    get_estop_api_estop_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EstopStatus"];
                };
            };
        };
    };
    engage_estop_api_estop_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["EngageRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EstopStatus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    clear_estop_api_estop_clear_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EstopStatus"];
                };
            };
        };
    };
    list_poses_api_poses_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Pose"][];
                };
            };
        };
    };
    create_pose_api_poses_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreatePose"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Pose"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    capture_pose_api_poses_capture_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["CapturePose"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Pose"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_pose_api_poses__pose_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                pose_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    patch_pose_api_poses__pose_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                pose_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PatchPose"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Pose"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    pose_links_api_poses__pose_id__links_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                pose_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PoseLinks"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    goto_pose_api_poses__pose_id__goto_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                pose_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["TriggerRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlaybackState"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_sequences_api_sequences_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SequenceSummary"][];
                };
            };
        };
    };
    create_sequence_api_sequences_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateSequence"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Sequence"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_sequence_api_sequences__sid__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Sequence"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_sequence_api_sequences__sid__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    patch_sequence_api_sequences__sid__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PatchSequence"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Sequence"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    execute_sequence_api_sequences__sid__execute_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["TriggerRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlaybackState"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_templates_api_templates_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SeqTemplate"][];
                };
            };
        };
    };
    create_template_api_templates_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateTemplate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SeqTemplate"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_template_api_templates__tid__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    instantiate_template_api_templates__tid__instantiate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tid: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InstantiateTemplate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Sequence"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_control_state_api_control_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlaybackState"];
                };
            };
        };
    };
    stop_execution_api_execute_stop_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlaybackState"];
                };
            };
        };
    };
    resume_execution_api_execute_resume_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlaybackState"];
                };
            };
        };
    };
    set_teaching_api_teach_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TeachRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlaybackState"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_resting_api_rest_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RestRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlaybackState"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_logs_api_logs_get: {
        parameters: {
            query?: {
                lines?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LogResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_tuning_api_config_tuning_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TuningState"];
                };
            };
        };
    };
    put_tuning_api_config_tuning_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TuningState"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    save_tuning_api_config_tuning_save_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TuningState"];
                };
            };
        };
    };
    reset_tuning_api_config_tuning_reset_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TuningState"];
                };
            };
        };
    };
    perturb_api_sim_perturb_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Perturbation"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    state_api_sim_state_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SimulationState"];
                };
            };
        };
    };
    page_viewer__get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/html": string;
                };
            };
        };
    };
    script_viewer_main_min_js_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    health_api_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
}
