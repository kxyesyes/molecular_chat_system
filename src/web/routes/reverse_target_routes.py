"""Reverse target route registration."""
import math

from fastapi import UploadFile, File, Form, HTTPException


def setup_reverse_target_routes(app, *, _support):
    """Register the original endpoints with dynamically resolved compatibility support."""
    @app.post("/api/reverse_target/predict")
    async def reverse_target_predict(
        smiles: str = Form(...),
        threshold: float = Form(0.6),
        top_k: int = Form(10),
        organism_filter: str = Form("")
    ):
        """反向寻靶预测API"""
        try:
            if not smiles or not smiles.strip():
                raise HTTPException(status_code=400, detail="SMILES字符串不能为空")
            
            if not math.isfinite(threshold) or threshold < 0 or threshold > 1:
                raise HTTPException(status_code=400, detail="阈值必须在0-1之间")
            
            if top_k < 1 or top_k > 100:
                raise HTTPException(status_code=400, detail="返回数量必须在1-100之间")
            
            def run_prediction():
                from src.reverse_target.predictor import get_predictor

                predictor = get_predictor()
                return predictor.predict(
                    smiles=smiles.strip(),
                    threshold=threshold,
                    top_k=top_k,
                    combine_by_target=True,
                    organism_filter=organism_filter,
                )

            results = await _support._invoke_in_threadpool(run_prediction)
            
            _support.logger.info(f"反向寻靶预测成功: 找到 {len(results)} 个靶点")
            
            return {
                "success": True,
                "count": len(results),
                "results": results
            }
            
        except HTTPException:
            raise
        except ValueError as e:
            _support.logger.warning(f"反向寻靶输入无效: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            _support.logger.error(f"反向寻靶预测失败: {e}")
            raise HTTPException(status_code=500, detail=f"预测失败: {str(e)}")
    
    @app.post("/api/reverse_target/batch_predict")
    async def reverse_target_batch_predict(
        file: UploadFile = File(...),
        threshold: float = Form(0.6),
        top_k: int = Form(10),
        organism_filter: str = Form("")
    ):
        """反向寻靶批量预测API"""
        try:
            if not math.isfinite(threshold) or threshold < 0 or threshold > 1:
                raise HTTPException(status_code=400, detail="阈值必须在0-1之间且为有限数值")
            if top_k < 1 or top_k > 100:
                raise HTTPException(status_code=400, detail="返回数量必须在1-100之间")

            content = await _support._read_upload_limited(file, "reverse target batch file")
            text = content.decode("utf-8")
            
            from src.reverse_target.batch_input import parse_batch_rows
            rows = parse_batch_rows(file.filename or "", text, max_rows=100)
            smiles_list = [row["smiles"] for row in rows]
            
            if not rows:
                raise HTTPException(status_code=400, detail="未能从文件中解析出有效的SMILES")

            def run_batch_prediction():
                from src.reverse_target.predictor import get_predictor

                predictor = get_predictor()
                return predictor.predict_batch(
                    smiles_list=smiles_list,
                    threshold=threshold,
                    top_k=top_k,
                    combine_by_target=True,
                    organism_filter=organism_filter,
                )

            results = await _support._invoke_in_threadpool(run_batch_prediction)
            if not isinstance(results, list) or len(results) != len(rows):
                raise HTTPException(
                    status_code=502,
                    detail="批量预测结果与输入行数不一致，已拒绝返回不完整结果",
                )
            for result, input_row in zip(results, rows):
                # Keep the source row identity explicit even when the
                # predictor changes its internal list representation.
                result["original_row_index"] = input_row["row_index"]
                result["row_index"] = input_row["row_index"]
            
            _support.logger.info(f"批量反向寻靶预测完成: {len(results)} 个分子")
            
            return {
                "success": True,
                "count": len(results),
                "row_count": len(rows),
                "results": results
            }
            
        except HTTPException:
            raise
        except ValueError as e:
            _support.logger.warning(f"批量反向寻靶输入无效: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            _support.logger.error(f"批量预测失败: {e}")
            raise HTTPException(status_code=500, detail=f"批量预测失败: {str(e)}")

    @app.get("/api/reverse_target/stats")
    async def get_reverse_target_stats():
        """获取反向寻靶数据库统计信息"""
        try:
            from src.reverse_target.predictor import get_predictor

            def load_stats():
                return get_predictor().get_stats()

            stats = await _support._invoke_in_threadpool(load_stats)
            return {"success": True, "stats": stats}
        except Exception as e:
            _support.logger.error(f"获取统计信息失败: {e}")
            raise HTTPException(status_code=500, detail=f"获取统计信息失败: {str(e)}")

    @app.get("/api/reverse_target/health")
    async def get_reverse_target_health():
        """轻量检查反向寻靶本地数据库状态，不加载大型指纹矩阵。"""
        try:
            from src.reverse_target.config import get_reverse_target_data_dir
            from src.reverse_target.health import inspect_reverse_target_database

            data_dir = get_reverse_target_data_dir()
            return inspect_reverse_target_database(data_dir)
        except Exception as e:
            _support.logger.error(f"反向寻靶健康检查失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"反向寻靶健康检查失败: {str(e)}")
    
    @app.get("/api/reverse_target/similar_molecules")
    async def get_similar_molecules(
        smiles: str,
        target_name: str,
        threshold: float = 0.6,
        limit: int = 50,
        organism_filter: str = ""
    ):
        """获取特定靶点的相似分子列表"""
        try:
            if not smiles or not smiles.strip():
                raise HTTPException(status_code=400, detail="SMILES字符串不能为空")
            
            if not target_name or not target_name.strip():
                raise HTTPException(status_code=400, detail="靶点名称不能为空")
            if not math.isfinite(threshold) or threshold < 0 or threshold > 1:
                raise HTTPException(status_code=400, detail="阈值必须在0-1之间且为有限数值")
            if type(limit) is not int or limit < 1 or limit > 100:
                raise HTTPException(status_code=400, detail="返回数量必须在1-100之间")
            
            from src.reverse_target.predictor import get_predictor

            def load_similar():
                predictor = get_predictor()
                return predictor.get_similar_molecules(
                    smiles=smiles.strip(),
                    target_name=target_name.strip(),
                    threshold=threshold,
                    limit=limit,
                    organism_filter=organism_filter,
                )

            results = await _support._invoke_in_threadpool(
                load_similar
            )
            
            return {
                "success": True,
                "count": len(results),
                "target_name": target_name,
                "query_smiles": smiles,
                "results": results
            }
            
        except HTTPException:
            raise
        except ValueError as e:
            _support.logger.warning(f"获取相似分子输入无效: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            _support.logger.error(f"获取相似分子失败: {e}")
            raise HTTPException(status_code=500, detail=f"获取失败: {str(e)}")

    # ==================== 3D 药效团 API ====================

    @app.post("/api/reverse_target/predict_3d")
    async def reverse_target_predict_3d(
        smiles: str = Form(...),
        threshold: float = Form(0.5),
        top_k: int = Form(10),
        max_refine: int = Form(50),
        alpha_2d: float = Form(0.4),
        alpha_3d: float = Form(0.6),
        organism_filter: str = Form(""),
    ):
        """
        2D+3D 级联药效团反向寻靶
        
        流程:
        1. 用 2D Morgan 指纹粗筛 top_k * 10 个候选分子
        2. 对候选分子进行 3D 构象生成 + 药效团打分
        3. 融合 2D+3D 分数进行重新排序，返回最终 top_k 靶点
        """
        try:
            if not smiles or not smiles.strip():
                raise HTTPException(status_code=400, detail="SMILES字符串不能为空")

            if not math.isfinite(threshold) or threshold < 0 or threshold > 1:
                raise HTTPException(status_code=400, detail="阈值必须在0-1之间且为有限数值")
            if not math.isfinite(alpha_2d) or not math.isfinite(alpha_3d) or alpha_2d < 0 or alpha_3d < 0:
                raise HTTPException(status_code=400, detail="3D融合权重必须为非负有限数值")
            if not math.isclose(alpha_2d + alpha_3d, 1.0, rel_tol=0.0, abs_tol=1e-6):
                raise HTTPException(status_code=400, detail="3D融合权重之和必须为1")
            if top_k < 1 or top_k > 100:
                raise HTTPException(status_code=400, detail="返回数量必须在1-100之间")

            if max_refine < 1 or max_refine > 100:
                raise HTTPException(status_code=400, detail="3D精修候选数必须在1-100之间")

            smiles = smiles.strip()
            candidate_limit = _support._get_pharm3d_candidate_pool_limit(top_k, max_refine)

            # Step 1: 2D 预筛选。返回数量(top_k)和 3D 精修数量(max_refine)解耦。
            from src.reverse_target.predictor import get_predictor

            def load_candidates():
                predictor = get_predictor()
                return predictor.get_raw_similar_molecules(
                    smiles=smiles,
                    threshold=prefilter_threshold,
                    limit=candidate_limit,
                    organism_filter=organism_filter,
                )

            # 降低阈值保证候选数量，提升召回
            prefilter_threshold = max(0.0, threshold - 0.2)
            raw_candidates = await _support._invoke_in_threadpool(load_candidates)
            raw_candidate_count = len(raw_candidates)
            unique_target_count_before_top_k = len(
                {
                    item.get("target_name") or item.get("target_id") or item.get("chembl_id")
                    for item in raw_candidates
                    if item.get("target_name") or item.get("target_id") or item.get("chembl_id")
                }
            )

            if not raw_candidates:
                return {
                    "success": True,
                    "mode": "2d+3d",
                    "count": 0,
                    "results": [],
                    "query_pharmacophore": None,
                    "warning": "未找到候选分子，请降低相似度阈值",
                    "requested_top_k": top_k,
                    "candidate_pool_size": candidate_limit,
                    "raw_candidate_count": 0,
                    "unique_target_count_before_top_k": 0,
                    "prefilter_threshold": prefilter_threshold,
                }

            _support.logger.info(
                f"3D精修: 2D粗筛得到 {len(raw_candidates)} 个候选分子，"
                f"候选池上限 {candidate_limit}，计划精修前 {min(max_refine, len(raw_candidates))} 个，"
                f"最终返回前 {top_k} 个"
            )

            # Step 2: 3D 药效团精修（在线程池中执行以避免阻塞事件循环）
            from src.reverse_target.pharmacophore_refiner import (
                refine_with_pharmacophore,
                get_molecule_pharmacophore,
            )

            refinement_status = "success"
            refinement_message = "3D 药效团精修完成"
            timeout_seconds = _support._get_pharm3d_timeout(25.0)

            try:
                outer_timeout = timeout_seconds + min(1.0, max(0.1, timeout_seconds * 0.2))
                refined = await _support._run_pharm3d_job(
                    lambda: refine_with_pharmacophore(
                        query_smiles=smiles,
                        candidates=raw_candidates,
                        max_to_refine=max_refine,
                        alpha_2d=alpha_2d,
                        alpha_3d=alpha_3d,
                        timeout_seconds=timeout_seconds,
                    ),
                    timeout_seconds=outer_timeout,
                )
            except _support.asyncio.TimeoutError:
                _support.logger.warning("3D药效团精修超时，返回2D基础结果")
                refinement_status = "timeout"
                refinement_message = "3D 药效团精修超时，已返回基础反向寻靶结果"
                refined = _support._build_pharm3d_fallback(raw_candidates, refinement_message)
                query_pharm = None
            except Exception as e:
                _support.logger.warning(f"3D药效团精修失败，返回2D基础结果: {e}")
                refinement_status = "fallback"
                refinement_message = f"3D 药效团精修失败，已返回基础反向寻靶结果: {str(e)}"
                refined = _support._build_pharm3d_fallback(raw_candidates, refinement_message)
                query_pharm = None
            else:
                timeout_fallback_count = sum(
                    1 for item in refined if item.get("pharm_refinement_status") == "timeout_fallback"
                )
                refined_count = sum(
                    1 for item in refined if item.get("pharm_refinement_status") == "refined"
                )
                not_refined_count = max(0, len(refined) - refined_count)
                if timeout_fallback_count:
                    refinement_status = "timeout_partial"
                    refinement_message = (
                        f"3D 药效团精修达到时间上限，已完成 {refined_count} 个候选；"
                        f"其余候选使用 2D 基础分数返回"
                    )
                elif not_refined_count:
                    refinement_status = "partial"
                    refinement_message = (
                        f"已对前 {refined_count} 个候选完成 3D 药效团精修；"
                        f"其余候选使用 2D 基础分数返回"
                    )

                try:
                    query_pharm = await _support._run_pharm3d_job(
                        lambda: get_molecule_pharmacophore(smiles),
                        timeout_seconds=min(5.0, timeout_seconds),
                    )
                except _support.asyncio.TimeoutError:
                    _support.logger.warning("查询分子药效团提取超时，继续返回精修结果")
                    query_pharm = None
                except Exception as e:
                    _support.logger.warning(f"查询分子药效团提取失败，继续返回精修结果: {e}")
                    query_pharm = None

            # Step 3: 按结果状态分别聚合，禁止把 2D fallback 当作 3D 结果排序。
            from src.reverse_target.predictor import _aggregate_by_target
            refined_rows = [
                row for row in refined
                if row.get("pharm_refinement_status") == "refined"
                and row.get("final_3d_score") is not None
            ]
            fallback_rows = [
                row for row in refined
                if row.get("pharm_refinement_status") != "refined"
            ]
            for row in fallback_rows:
                # Treat the refiner as an untrusted boundary: an adapter or
                # legacy implementation must not smuggle a 2D value through
                # the 3D score field.
                row["final_3d_score"] = None
                row["rank_score"] = row.get("final_similarity", 0.0)
                row["score_semantics"] = "2d_similarity_fallback"
                row["pharm_combined_3d"] = None
                row["pharm_similarity"] = None
                row["alignment_score"] = None
                row["spatial_score"] = None
                row["alignment_rmsd"] = None
                row["alignment_pairs"] = []
                row["alignment_coverage"] = 0.0
                row["pharm_score_method"] = None
                row["pharm_matching_method"] = None
                row["pharm_features"] = []
            refined_results = _aggregate_by_target(
                refined_rows, top_k=top_k, score_field="final_3d_score"
            ) if refined_rows else []
            fallback_results = _aggregate_by_target(
                fallback_rows, top_k=top_k, score_field="final_similarity"
            ) if fallback_rows else []
            fallback_count = len(fallback_results)
            if refined_results:
                final_results = refined_results
                ranking_mode = "3d_refined_with_2d_fallback" if fallback_results else "3d_refined"
            else:
                # If no candidate reached a valid 3D result, expose the 2D
                # ranking explicitly instead of returning an empty answer.
                final_results = fallback_results
                fallback_results = []
                ranking_mode = "2d_fallback"

            _support.logger.info(f"3D精修完成, 返回 {len(final_results)} 个靶点")

            return {
                "success": True,
                "mode": "2d+3d",
                "count": len(final_results),
                "results": final_results,
                "fallback_results": fallback_results,
                "ranking_mode": ranking_mode,
                "fallback_count": fallback_count,
                "query_pharmacophore": query_pharm if query_pharm and query_pharm.get("success") else None,
                "pharmacophore_refinement_status": refinement_status,
                "message": refinement_message,
                "requested_top_k": top_k,
                "candidate_pool_size": candidate_limit,
                "raw_candidate_count": raw_candidate_count,
                "unique_target_count_before_top_k": unique_target_count_before_top_k,
                "prefilter_threshold": prefilter_threshold,
            }

        except HTTPException:
            raise
        except ValueError as e:
            _support.logger.warning(f"3D药效团预测输入无效: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            _support.logger.error(f"3D药效团预测失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"3D预测失败: {str(e)}")

    @app.post("/api/reverse_target/pharmacophore")
    async def get_pharmacophore(
        smiles: str = Form(...),
    ):
        """
        单分子 3D 药效团特征提取 API
        
        返回:
            features      : 药效团特征列表（含坐标、类型、颜色）
            feature_counts: 各类特征数量统计
            mol_block     : MolBlock (供 3Dmol.js 可视化)
            properties    : 理化性质 (MW, LogP, TPSA, HBD, HBA, RotBonds)
        """
        try:
            if not smiles or not smiles.strip():
                raise HTTPException(status_code=400, detail="SMILES字符串不能为空")

            from src.reverse_target.pharmacophore_refiner import get_molecule_pharmacophore

            try:
                result = await _support._run_pharm3d_job(
                    lambda: get_molecule_pharmacophore(smiles.strip()),
                    timeout_seconds=_support._get_pharm3d_timeout(25.0),
                )
            except _support.asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="药效团提取超时，请稍后重试或使用更简单的分子")

            if not result.get("success"):
                raise HTTPException(status_code=422, detail=result.get("error", "药效团提取失败"))

            return result

        except HTTPException:
            raise
        except Exception as e:
            _support.logger.error(f"药效团提取失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"药效团提取失败: {str(e)}")
